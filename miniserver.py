#!/usr/bin/env python
#coding=utf-8
import os, io, sys, math, requests, json, re, base64, logging, shutil
import time, ctypes, socket, threading, json, sqlite3, hashlib, traceback
from threading import Thread
from random import randint
from io import StringIO, BytesIO

if (sys.version_info < (3, 0)):
    from BaseHTTPServer import HTTPServer
    from SimpleHTTPServer import SimpleHTTPRequestHandler
    from SocketServer import ThreadingMixIn
else:
    from http.server import HTTPServer, BaseHTTPRequestHandler, SimpleHTTPRequestHandler
    from socketserver import ThreadingMixIn








#############################################################################
#############################################################################
class YunDisk:
    def __init__(self, conn):
        self._conn = conn

    def totalSize(self):
        # 获取所有文件数量 文件大小
        command = 'select count(FTYPE) from FILES where FTYPE=1'
        cu = self._conn.cursor()
        cu.execute(command)
        record = cu.fetchone()
        cu.close()
        # 文件总数量
        count = int(record[0])
        #
        command = 'select sum(FSIZE) from FILES where FTYPE=1'
        cu = self._conn.cursor()
        cu.execute(command)
        record = cu.fetchone()
        cu.close()
        size = int(record[0])
        #
        return (count, size)


    def listDir(self, folderid):
        # 列出目录中的文件
        files = []
        folder = { 'code': 0, 'msg': '', 'files': files }

        # 先读取文件夹的信息
        if (folderid == ''): folderid = '00000000000000000000000000000000'
        if (folderid == '00000000000000000000000000000000'):
            # 根目录信息
            fcount, fsize = self.totalSize()
            folder['fid'] = '00000000000000000000000000000000'
            folder['pid'] = '00000000000000000000000000000000'
            folder['fname'] = '/'
            folder['ftype'] = 0
            folder['fsize'] = fsize
            folder['fdate'] = 0
            folder['fcount'] = fcount
            folder['state'] = None
        else:
            # 普通目录
            command = 'select FID, PID, FNAME, FTYPE, FSIZE, FDATE, STATE from FILES where FID=?'
            args = (folderid,)
            cu = self._conn.cursor()
            cu.execute(command, args)
            record = cu.fetchone()
            cu.close()
            if (record):
                folder['fcount'] = 0
                folder['fid'] = record[0]
                folder['pid'] = record[1]
                folder['fname'] = record[2]
                folder['ftype'] = record[3]
                folder['fsize'] = record[4]
                folder['fdate'] = record[5]
                folder['state'] = record[6]
            else:
                folder['code'] = 400
                folder['msg'] = 'folder not found.'
                return folder


        # 再读取文件夹下面文件的信息
        command = 'select FID, PID, FNAME, FTYPE, FSIZE, FDATE, STATE from FILES where PID=? order by FTYPE, FNAME'
        args = (folderid,)
        cu = self._conn.cursor()
        cu.execute(command, args)
        records = cu.fetchall()
        cu.close()
        #

        for row in records:
            item = {
                'fid': row[0],
                'pid': row[1],
                'fname': row[2],
                'ftype': row[3],
                'fsize': row[4],
                'fdate': row[5],
                'state': row[6]
            }
            files.append(item)
            if (folder['fname'] != '/'):
                folder['fcount'] = folder['fcount'] + 1
                folder['fsize'] = folder['fsize'] + int(row[4])
        # 返回结果
        return folder

    def fileMeta(self, fileid):
        # 获取文件的元数据
        fileinfo = { 'code': 0, 'msg': '' }
        command = 'select FID, PID, FNAME, FTYPE, FSIZE, FDATE, MIME, STATE from FILES where FID=?'
        args = (fileid,)
        cu = self._conn.cursor()
        cu.execute(command, args)
        record = cu.fetchone()
        cu.close()
        if (record):
            fileinfo['fid'] = record[0]
            fileinfo['pid'] = record[1]
            fileinfo['fname'] = record[2]
            fileinfo['ftype'] = record[3]
            fileinfo['fsize'] = record[4]
            fileinfo['fdate'] = record[5]
            fileinfo['mime'] = record[6]
            fileinfo['state'] = record[7]
        else:
            fileinfo['code'] = 400
            fileinfo['msg'] = 'cannot found file: %s' % fileid
        # 返回结果
        return fileinfo

    def readData(self, writer, fid, start, size, fast=False):
        # 下载流文件 或者下载分段
        # fid: 文件FID
        # start: 开始偏移量
        # size: 小于0返回数据到结束
        # fast: 使用多线程下载

        #result = [
        #    {'pid': '', 'range': [0, 100], 'head': 82},
        #    {'pid': '', 'range': [100, 200], 'head': 82}
        #         ]
        # 从数据库计算分块信息
        if (size == 0): return None
        if (size < 0): end = -1
        else: end = start + size
        blocks = self.fetchData(fid, start, end)
        # 构造下载任务
        tasks = self.buildTask(blocks, start, end)
        if (tasks == None): return None
        # 下载并且放到输出流中 --
        if (fast == False): self.doTasks(fid, tasks, writer, 4096)
        else: self.doTasksFast(fid, tasks, writer, 4096)
        # 返回结果
        return tasks

    def fetchData(self, fid, start, end):
        # 从数据库获取记录
        # 注意 [start, end) 不包含end
        # end 小于0返回到文件末尾
        ftable = '_' + fid
        command = 'select count(*) from FILES where FID=?'
        args = (fid,)
        cu = self._conn.cursor()
        cu.execute(command, args)
        record = cu.fetchone()
        if (record[0] == 0): return []

        # 取出记录
        # result = [
        #    {'pid': '', 'range': [0, 100], 'head': 82},
        #    {'pid': '', 'range': [100, 200], 'head': 82}
        #         ]
        result = []
        sql = 'select PID, FSTART, FEND, HEADSIZE, PDATE from {0} where ' \
              '(FSTART<? or ?<0) and FEND>=? order by FSTART desc'.format(ftable)
        args = (end, end, start)
        cu.execute(sql, args)
        records = cu.fetchall()
        for row in records:
            # 遍历记录
            pid = row[0]
            fstart = row[1]
            fend = row[2]
            headsize = row[3]
            pdate = row[4]
            block = {'pid': pid, 'range': [fstart, fend], 'head': headsize}
            result.append(block)
        cu.close()
        #
        return result

    def buildTask(self, blocks, start, end):
        # 检查 和 构造下载任务
        # 数据必须是连续的 否则返回空
        # tasks = {
        #    'size': 0,
        #    'start': 0,
        #    'tasks': [
        #        {'pid': '', 'index': 0, 'range': [0, 0]},
        #        {'pid': '', 'index': 1, 'range': [0, 0]}
        #    ]
        # }
        tasks = {
            'size': 0,
            'start': start,
            'tasks': []
        }
        # 按照块的开始排序 块必须 收尾相接 否则返回错误
        blocks.sort(key=lambda item: item['range'][0], reverse=False)
        index = 0
        lastbs = 0
        size = 0
        for block in blocks:
            bs = block['range'][0]
            be = block['range'][1]
            pid = block['pid']
            head = block['head']

            # 文件相对偏移量
            offsets = head
            offsete = head + be - bs

            # 算法没有验证过 没有做过仔细检查
            # 计算偏移量 ~乱
            if (be <= start):
                # 1. 不在范围内
                continue
            elif (bs < end or end < 0):
                # 2. 落在块区间里 计算相对偏移量
                if (bs < start): offsets += start - bs
                if (be > end and end >= 0): offsete -= be - end
            elif (bs >= end and end >= 0):
                # 3. 不在范围内
                continue
            else:
                # 4. 考虑不周
                raise Exception()

            #
            index += 1
            task = {'pid': pid, 'index': index, 'range': [offsets, offsete]}
            if (lastbs > 0 and lastbs != bs):
                # 数据不连续返回空
                return None

            # 添加一条任务
            size += offsete - offsets
            tasks['tasks'].append(task)

        tasks['size'] = size

        return tasks

    def doTasks(self, fid, tasks, writer, buff):
        # 下载任务
        # tasks: 任务列表
        # writer: 输出流
        # buff: 每隔多少刷新一次
        # tasks = {
        #    'size': 0,
        #    'start': 0,
        #    'tasks': [
        #        {'pid': '', 'index': 0, 'range': [0, 0]},
        #        {'pid': '', 'index': 1, 'range': [0, 0]}
        #    ]
        # }
        # 按照index排序
        size = tasks['size']
        tasklist = tasks['tasks']
        tasklist.sort(key=lambda item: item['index'], reverse=False)
        index = 0
        for task in tasklist:
            # 循环任务
            index = index + 1
            pid = task['pid']
            index = task['index']
            start = task['range'][0]
            end = task['range'][1]
            #
            logging.info('downloading - [%d/%d] pid:%s [%d, %d]' % (index, len(tasklist), pid, start, end))

            url = 'https://ae01.alicdn.com/kf/{0}.jpg'.format(pid)
            headers = {'Range': 'bytes=%d-%d' % (start, end)}

            # HTTP 200 获取全部数据
            # HTTP 206 获取部分数据

            # 为了保证返回数据正确 尝试3次请求 如果得不到206结果抛出异常
            r = requests.get(url, headers=headers, stream=True)
            if (r.status_code != 206):
                r.close()
                # 第二次请求
                r = requests.get(url, headers=headers, stream=True)
                if (r.status_code != 206):
                    r.close()
                    # 第三次请求
                    r = requests.get(url, headers=headers, stream=True)
                    if (r.status_code != 206 and r.status_code != 200):
                        r.close()

            # 读取数据
            if (r.status_code == 200):
                # 返回结果200需要自己过滤有效数据
                index = 0
                for chunk in r.iter_content(chunk_size=buff):
                    # 分块下载
                    if chunk:  # filter out keep-alive new chunks
                        size = len(chunk)

                        data = None
                        newstart = 0
                        newend = size
                        # 算法没有验证过 没有做过仔细检查
                        if (end <= index): break                    # 已经结束 不在范围内
                        elif (start < index + size):                # 落在区间内
                            if (start > index): newstart = start - index
                            if (end < index + size): newend = index + size - end
                        elif (start >= index + size): continue      # 还没开始
                        else: raise Exception()                     # 考虑不周
                        #
                        data = chunk[newstart: newend]

                        try:
                            writer.write(data)
                            # writer.flush()
                        except socket.error:
                            logging.warning('req terminated - pid:%s' % pid)
                            r.close()
                            return None
                        except Exception as ex:
                            logging.error('%s - pid:%s' % (ex, pid))
                            r.close()
                            return None
            # 读取206数据
            elif (r.status_code == 206):
                # 返回结果200数据已经过滤好了 直接写入流里
                for chunk in r.iter_content(chunk_size=buff):
                    # 分块下载
                    if chunk:  # filter out keep-alive new chunks
                        try:
                            writer.write(chunk)
                            # writer.flush()
                        except socket.error:
                            logging.warning('req terminated - pid:%s' % pid)
                            r.close()
                            return None
                        except Exception as ex:
                            logging.error('%s - pid:%s' % (ex, pid))
                            r.close()
                            return None

            logging.info('downloaded - pid:%s' % pid)

    def doTasksFast(self, fid, tasks, writer, buff):
        # 快速下载没有完成
        logging.error('undo fast function')
        raise Exception()

#############################################################################
#############################################################################


class PartialContentHandler(SimpleHTTPRequestHandler):
    # HTTP服务器主体
    def __init__(self, request, client_address, server):
        # 初始化
        SimpleHTTPRequestHandler.__init__(self, request, client_address, server)

    def do_GET(self):
        # 处理GET请求
        data = self.route()
        if (data): self.send_datas(data)
        else: self.send_error(404, '%s file not found.' % self.path)    # 返回404页面

    def send_datas(self, f):
        # 数据发送
        try:
            self.copyfile(f, self.wfile)
        except socket.error:
            self.log_message('%s req terminated.', self.requestline)
        finally: f.close()

    def sizeToStr(self, size):
        # 文件大小转
        if (size > 1024**3): return '%.2fG' % (size / (1024.0**3))
        elif (size > 1024**2): return '%.2fM' % (size / (1024.0**2))
        elif (size > 1024**1): return '%.2fK' % (size / (1024.0**1))
        else: return '%dB' % size

    def route(self):
        # 路由 核心处理
        action, id = os.path.split(self.path.lower())

        # 路由解析 返回的都是 ByteIO 对象 返回前需要先设置头部
        if (action == '/'):
            # 首页
            return self.list_files('')
        elif (action == '/list'):
            # 列出当前目录
            return self.list_files(id)
        elif (action == '/file'):
            # 返回文件
            return self.read_file(id)
        elif (action == '/test'):
            # 列出当前目录
            self.send_response(200)
            self.end_headers()
            f = BytesIO()
            f.write(id.encode('utf8'))
            f.seek(0)
            return f
        else:
            return None

    def list_files(self, fid):
        # 列出目录下的文件
        folder = disk.listDir(fid)
        if (folder['code'] != 0):
            # 文件夹没有找到
            self.send_error(404, folder['msg'])
            return None

        # 先列出当前目录信息
        html = ''
        html += u'<html><head><meta charset="utf-8"><title>DODO DISK</title></head><body>'
        html += u'<a href="/list/%s">上级目录</a>' % folder['pid']
        html += u' 当前目录: %s' % folder['fname']
        html += u'<div style="float: right;">大小: %s 数量: %s</div>' % (self.sizeToStr(folder['fsize']), folder['fcount'])
        html += u'<br><hr>'
        html += u'<table border="1" style="width:100%">'
        html += u'<tr><th>名称</th> <th>类型</th> <th>大小</th></tr>'
        # 列出目录里的文件和文件夹
        for item in folder['files']:
            if (item['ftype'] == 0):
                # 文件夹
                html += u'<tr>' \
                        u'<td>目录</td>' \
                        u'<td><a href="/list/{0}">{1}</a></td>' \
                        u'<td></td>' \
                        u'</tr>'.format(
                    item['fid'], item['fname'])
            else:
                # 文件
                html += u'<tr>' \
                        u'<td>文件</td>' \
                        u'<td><a href="/file/{0}">{1}</a></td>' \
                        u'<td>{2}</td>' \
                        u'</tr>'.format(
                    item['fid'], item['fname'], self.sizeToStr(item['fsize']))

        html += u'</table>'
        html += u'</body></html>'

        # 构造输出流
        stream = BytesIO()
        stream.write(b'')
        stream.write(html.encode('utf8'))

        #
        length = stream.tell()
        stream.seek(0)
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.send_header("Content-Length", length)
        self.end_headers()
        return stream

    def read_file(self, fid, fname=None):
        # 读取文件
        #
        # 先获取原文件的信息
        metadata = disk.fileMeta(fid)
        if (metadata['code'] != 0):
            # 没有找到文件
            self.send_error(404, metadata['msg'])
            return None
        # 元数据
        fsize = metadata['fsize']
        if (not fname): fname = metadata['fname']
        mime = metadata['mime']
        if (not mime): mime = 'application/octet-stream'
        #
        if self.headers.get("Range"):
            # 断点续传 206
            start = self.headers.get("Range")
            try:
                m = re.match('.*=(\d+)-(\d*)', start)
                pos = m.group(1)
                end = m.group(2)
            except ValueError:
                self.send_error(400, "bad range specified.")
                return None
            # 偏移量
            pos = int(pos)
            if (end == '' or end == None): end = fsize

            # 构造头部 返回206数据
            self.send_response(206)
            self.send_header("Content-type", mime)
            self.send_header("Connection", "keep-alive")
            # self.send_header("Accept-Ranges", 'bytes')
            self.send_header("Content-Length", str(end - pos))
            self.send_header("Content-Range", "bytes %s-%s/%s" % (pos, end - 1, end))
            # self.send_header("Content-Disposition", "attachment;filename=%s" % fname)
            self.end_headers()

            # 写数据流
            try:
                disk.readData(self.wfile, fid, pos, -1)
            except Exception as ex:
                print(ex)
                print(traceback.print_exc())
        else:
            # 正常返回数据200
            self.send_response(200)
            self.send_header("Connection", "keep-alive")
            self.send_header("Content-type", mime)
            self.send_header("Content-Length", str(fsize))
            # self.send_header("Content-Disposition", "attachment;filename=%s" % fname)
            # self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
            self.end_headers()
            try:
                disk.readData(self.wfile, fid, 0, -1)
            except Exception as ex:
                print(ex)
                print(traceback.print_exc())
        #
        return None



class NotracebackServer(HTTPServer):
    # could make this a mixin, but decide to keep it simple for a simple script.
    def handle_error(self, *args):
        # override default function to disable traceback.
        pass

class ThreadingServer(ThreadingMixIn, HTTPServer):
    # 多线程
    pass

def main(port, multithread=True, server_class=NotracebackServer, handler_class=PartialContentHandler):
    # 主程序入口
    server_address = ('0.0.0.0', port)
    if (multithread == False):  # 单线程
        httpd = server_class(server_address, handler_class)
        httpd.serve_forever()
    else:                       # 多线程
        srvr = ThreadingServer(server_address, handler_class)
        srvr.serve_forever()

disk = None

if __name__ == "__main__":
    #
    port = randint(20000, 50000)
    port = 5555
    conn = sqlite3.connect('data.db', check_same_thread=False)
    disk = YunDisk(conn)

    print()
    print("started on http://localhost:%s/" % (port))
    print("===== DODO DISK =====\n")
    main(port=port)

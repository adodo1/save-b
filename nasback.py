#!/usr/bin/env python
# encoding: utf-8
import os, sys, io, sqlite3, math, smtplib, hashlib, time, mimetypes, binascii, requests, json
from logger import logger
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# 1. 扫描采用2种模式
#    快速: 只对比文件名
#    完整: 对比 文件名 > 文件大小 > MD5值
# 2. 上传文件 判断是否上传成功
# 3. 重试次数 超过报错
# 4. 邮件通知
# 5. 创建图片视频预览
# 6. 实现打包下载 批量下载
# 7. 搜索功能
# 8. 多节点冗余
# 9.

# 配置信息
SYNC_DIR = ''                   # 同步目录
DATABASE = ''                   # 数据库
RECEIVERS = []                  # 邮件通知
SCAN_MODLE= 0                   # 扫描模式 0:快速 1:完整


# 邮件配置
MAIL_HOST = "smtp.126.com"          # SMTP服务器
MAIL_USER = "dodomails"             # 用户名
MAIL_PASS = "adodo1"                # 密码(这里的密码不是登录邮箱密码，而是授权码)
MAIL_SENDER = 'dodomails@126.com'   # 发件人邮箱




# 1. 扫描模块
# 2. 上传模块
# 3. 邮件模块
# 4. 读取cookie模块


class DatabaseClass:
    # 数据库类
    def __init__(self, dbfile):
        self._conn = sqlite3.connect(dbfile, check_same_thread = False)
        self._initdb()

    def _initdb(self):
        # 初始化数据库
        cursor = self._conn.cursor()

        # 创建FILES表
        cursor.execute(
            """
CREATE TABLE IF NOT EXISTS [FILES] (
  [FID] CHAR(32),           -- 文件或目录ID
  [PID] CHAR(32),           -- 父目录ID
  [FNAME] TEXT,             -- 文件名或目录名
  [FTYPE] INT,              -- 文件类型 目录或文件
  [FSIZE] INT,              -- 文件大小
  [FDATE] INT,              -- 文件日期
  [OWNER] CHAR(32),         -- 文件所有者UID
  [PERMISSION] INT,         -- 权限
  [ATTR] INT,               -- 文件属性
  [MIME] CHAR(64),          -- 数据类型
  [STATE] INT,              -- 状态
  [METADATA] CLOB);         -- 文件元数据
            """)

        # 创建索引
        commands = """
CREATE INDEX IF NOT EXISTS [INDEX_FILES_FID] ON [FILES] ([FID]);
CREATE INDEX IF NOT EXISTS [INDEX_FILES_PID] ON [FILES] ([PID]);
CREATE INDEX IF NOT EXISTS [INDEX_FILE_OWNER] ON [FILES] ([OWNER]);
CREATE INDEX IF NOT EXISTS [INDEX_FILES_STATE] ON [FILES] ([STATE]);
CREATE INDEX IF NOT EXISTS [INDEX_FILES_DATE] ON [FILES] ([FDATE]);
CREATE INDEX IF NOT EXISTS [INDEX_FILES_FNAME] ON [FILES] ([FNAME]);
        """
        #
        for command in commands.split(';'):
            command = command.strip()
            if (command == ''): continue
            cursor.execute(command)

        # 保存
        self._conn.commit()

    def fileMD5(self, fname):
        # 计算文件MD5值
        if not os.path.isfile(fname): return None
        fhash = hashlib.md5()
        f = open(fname, 'rb')
        while True:
            b = f.read(8192)
            if not b: break
            fhash.update(b)
        f.close()
        fmd5 = fhash.hexdigest().lower()
        # logger.info('md5 - %s: %s', fmd5, fname)
        return fmd5

    def openDir(self, dirname):
        # 创建或打开目录 递归的方式
        dirname = dirname.replace('\\', '/') + '/'
        dirname = dirname.lower()
        if (dirname.startswith('/')==False): dirname = '/' + dirname
        dirname = os.path.abspath(dirname)
        index = dirname.rfind(':')
        if (index>=0): dirname = dirname[index+1:]
        #
        md5hash = hashlib.md5(dirname.encode('utf8'))
        fid = md5hash.hexdigest().lower()
        #
        command = r'select FID, PID, FNAME from FILES where FID=?'
        cursor = self._conn.cursor()
        cursor.execute(command, (fid,))
        record = cursor.fetchone()
        cursor.close()
        if (record): return fid
        else:
            # 递归创建目录
            parent, current = os.path.split(dirname)
            if (not current): return '00000000000000000000000000000000'
            pid = self.openDir(parent)
            # 创建当前目录
            command = r'insert into FILES(FID, PID, FNAME, FTYPE, FSIZE, FDATE) values(?,?,?,?,?,?)'
            args = (fid, pid, current, 0, 0, int(time.time()))
            cursor = self._conn.cursor()
            cursor.execute(command, args)
            self._conn.commit()
        # 返回当前ID
        return fid

    def createFile(self, sfile):
        # 向数据库添加一个新文件信息
        fsize = os.path.getsize(sfile)              # 文件大小
        fdir, fname = os.path.split(sfile)          # 文件路径
        fdate = int(os.path.getctime('data.db'))    # 文件修改时间 int(time.time())
        # 计算FID 并创建目录
        fid = self.fileMD5(sfile)
        pid = self.openDir(fdir)
        fmime = mimetypes.guess_type(fname)[0]
        if (not fmime): fmime = 'application/octet-stream'
        tabname = '_' + fid
        #
        command = r'select STATE from FILES where FID=? and PID=? and FNAME=?'
        args = (fid, pid, fname)
        cu = self._conn.cursor()
        cu.execute(command, args)
        record = cu.fetchone()

        # 插入记录
        if (not record):
            # 没有记录 添加一条文件记录
            command = r'insert into FILES(FID, PID, FNAME, FTYPE, FSIZE, FDATE, MIME, STATE) ' \
                      r'values(?,?,?,?,?,?,?,?)'
            args = (fid, pid, fname, 1, fsize, fdate, fmime, 0)
            cu.execute(command, args)
            self._conn.commit()
        elif (int(record[0]) == 0):
            # 文件已经存在 正在上传
            return False, fid
        else:
            # 文件记录已存在 上传完成或者处于其他状态 比如删除状态
            return True, fid


        # 创建文件块信息表
        command = r"""
                CREATE TABLE IF NOT EXISTS [{0}] (
                  [PID] CHAR(100), 
                  [FSTART] INT, 
                  [FEND] INT, 
                  [HEADSIZE] INT, 
                  [PDATE] INT, 
                  [STATE] INT)
        """.format(tabname)
        cu.execute(command)
        #
        self._conn.commit()
        cu.close()
        #
        return False, fid

    def hasPart(self, fid, start, end):
        # 判断数据库里是否已经有 如果有而且状态为[100]就跳过
        tabname = '_' + fid
        sql = r'select PID, FSTART, FEND, HEADSIZE, PDATE, STATE from {0} where FSTART=? and FEND=?'.format(tabname)
        args = (start, end)
        cu = self._conn.cursor()
        cu.execute(sql, args)
        record = cu.fetchone()
        cu.close()

        if (record):
            pid = record[0]
            start = record[1]
            end = record[2]
            headsize = record[3]
            pdate = record[4]
            state = record[5]
            if (state == 100):
                block = {'pid': pid, 'range': [start, end], 'error': '', 'head': headsize}
                return block
        # 否则返回空
        return None

    def insertPart(self, fid, pid, start, end, headsize, state):
        # 插入块信息
        tabname = '_' + fid
        pdate = int(time.time())

        sql = r'select STATE from {0} where FSTART=? and FEND=?'.format(tabname)
        args = (start, end)
        cu = self._conn.cursor()
        cu.execute(sql, args)
        record = cu.fetchone()
        #
        if (record):
            # 更新原有记录
            sql = r'update {0} set PID=?, HEADSIZE=?, PDATE=?, STATE=? where FSTART=? and FEND=?'.format(tabname)
            args = (pid, headsize, pdate, state, start, end)
            cu.execute(sql, args)
            self._conn.commit()
        else:
            # 插入新纪录
            sql = r'insert into {0}(PID, FSTART, FEND, HEADSIZE, PDATE, STATE) values(?,?,?,?,?,?)'.format(tabname)
            args = (pid, start, end, headsize, pdate, state)
            cu.execute(sql, args)
            self._conn.commit()
        #
        cu.close()

    def markSuccess(self, fid):
        # 文件状态标记成功
        command = r'update FILES set STATE=100 where FID=?'
        args = (fid,)
        cu = self._conn.cursor()
        cu.execute(command, args)
        cu.close()
        self._conn.commit()

class YunDiskClass:
    # 云盘 学习借鉴baidu pcs api
    # 块大小 字节
    BLOCK_SIZE = 2 * 1024 * 1024
    # 基础图片
    # BASE_DATA = binascii.unhexlify(
    #            '89504E470D0A1A0A0000000D49484452' \
    #            '0000000100000001010300000025DB56' \
    #            'CA00000003504C5445FFFFFFA7C41BC8' \
    #            '0000000A4944415408D7636000000002' \
    #            '0001E221BC330000000049454E44AE42' \
    #            '6082')

    BASE_DATA = binascii.unhexlify(
                'FFD8FFE1001845786966000049492A00' \
                '080000000000000000000000FFEC0011' \
                '4475636B7900010004000000000000FF' \
                'EE000E41646F62650064C000000001FF' \
                'DB0084001B1A1A291D2941262641422F' \
                '2F2F42473F3E3E3F4747474747474747' \
                '47474747474747474747474747474747' \
                '47474747474747474747474747474747' \
                '47474747011D29293426343F28283F47' \
                '3F353F47474747474747474747474747' \
                '47474747474747474747474747474747' \
                '47474747474747474747474747474747' \
                '4747474747FFC0001108000100010301' \
                '2200021101031101FFC4004B00010100' \
                '00000000000000000000000000000601' \
                '01000000000000000000000000000000' \
                '00100100000000000000000000000000' \
                '00000011010000000000000000000000' \
                '0000000000FFDA000C03010002110311' \
                '003F00A6001FFFD9')

    #
    HEADER_SIZE = len(BASE_DATA)    # 数据头大小

    def __init__(self, cookie, database):
        # 上传才需要cookie
        self._cookie = cookie
        self._db = database

    def UPFile(self, fname):
        # 上传文件到指定FID中
        # 先创建一个文件记录
        # 然后往块表中写数据
        # 最后更新文件记录状态
        has, fid = self._db.createFile(fname)
        if (has): return fid
        # 正式上传
        logger.info('current file: %s' % fname)
        result = self.UPPart(fid, fname, 0, -1)
        # 标记是否成功
        if (result and result['success']):
            self._db.markSuccess(fid)
        return fid

    def UPPart(self, fid, fname, start, size):
        # 分段上传 返回文件fid
        # fname: 文件名
        # start: 开始位置
        # size: 读取大小
        if (not fid): return None

        # 加上逻辑 如果没有文件块表创建
        f = open(fname, 'rb')
        # 获取文件的大小
        f.seek(0, io.SEEK_END)
        fsize = f.tell()
        # 开始位置已经超出文件大小
        if (start >= fsize): return None
        if (fsize == 0): return None
        if (start < 0): return None
        # 允许size小于0
        if (size <= 0): size = fsize
        # 计算结束位置
        # 注意包含开始但是不包含结束 [start, end)
        end = min(fsize, start + size)
        # 跳转到开始位置
        f.seek(start)

        #
        # result = {
        #    'fid': '',
        #    'blocks': [
        #        {'pid': '', 'range':[0, 100], 'error': '', 'head': 82},
        #        {'pid': '', 'range':[100, 200], 'error': '', 'head': 82}
        #    ]
        # }
        result = {
            'fid': fid,
            'fsize': fsize,
            'blocks': []
        }

        success = True
        num = 0
        count = math.ceil(size * 1.0 / self.BLOCK_SIZE)
        while (True):
            #
            num += 1
            index = f.tell()
            if (index >= end): break
            blocksize = self.BLOCK_SIZE  # 本次读取的块大小
            if (index + blocksize > end):
                blocksize = end - index

            data = f.read(blocksize)
            msize = len(data)

            # 处理逻辑 print index, msize
            # --------------------------------------------------------------
            # 先判断数据库里是否已经有上传好的数据
            # 如果已经有上传跳过
            block = self._db.hasPart(fid, index, index + msize)
            if (block != None):
                result['blocks'].append(block)
                continue

            #
            # logger.info('uploading - [%d/%d] fid:%s [%d, %d]' % (num, count, fid, index, index + msize))
            pid = self.UPData(data)
            if (pid != None):
                # 成功
                logger.info('[%04d/%d] success - size:%010d pid:%s' % (num, count, len(data), pid))

                block = {'pid': pid, 'range': [index, index + msize], 'error': '', 'head': self.HEADER_SIZE}
                result['blocks'].append(block)
                self._db.insertPart(fid, pid, index, index + msize, self.HEADER_SIZE, 100)      # 插入数据库
            else:
                # 失败
                logger.error('[%04d/%d] error - size:%010d' % (num, count, len(data)))
                success = False

                block = {'pid': '', 'range': [index, index + msize], 'error': 'upload fail.', 'head': self.HEADER_SIZE}
                result['blocks'].append(block)
                self._db.insertPart(fid, '', index, index + msize, self.HEADER_SIZE, 0)         # 插入数据库

            # --------------------------------------------------------------

        # 标记上传结果是否成功
        result['success'] = success
        # 返回上传结果
        return result

    def UPData(self, data):
        # 上传数据 通过阿里的接口
        url = 'https://kfupload.alibaba.com/mupload'
        #
        headers = {
            'Accept': '*/*',
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36',
            'Accept-Encoding': 'gzip, deflate',
            'Cookie': self._cookie
        }
        # 将数据追加到图片数据的结尾
        fulldata = self.BASE_DATA + data
        files = {'name': 'DODO.JPG',
                 'scene': 'productImageRule',
                 'file': ('DODO.JPG', fulldata, 'image/jpeg', {'Expires': '0'})}
        # 上传文件
        r = requests.post(url, files=files, headers=headers, allow_redirects=False)
        if (r.status_code != 200):
            logger.error('html code: %s' % r.status_code)
            return None
        result = json.loads(r.text)

        # 检验结果
        if (int(result['code']) != 0):
            logger.error('result error code: %s' % r.result['code'])
            return None
        #
        size = int(result['size'])
        pid = result['fs_url'][0:-4]

        # 采用简单验证
        if (size == len(fulldata)): return pid
        else: return None

        # 采用复杂验证
        # if (self.validateData(pid, len(fulldata))): return pid
        # else: return None

    def validateData(self, pid, size):
        # 验证数据有效性 主要通过文件大小判断
        if (pid == None or pid == ''): return False
        url = 'https://ae01.alicdn.com/kf/{0}.jpg'.format(pid)
        response = requests.head(url)
        content_len = response.headers['Content-Length']
        if (content_len == None): return False
        content_len = int(content_len)
        return content_len == size

    def listDir(self, folderid):
        # 列出目录中的文件
        files = []
        folder = { 'code': 0, 'msg': '', 'files': files }

        # 先读取文件夹的信息
        if (folderid == ''): folderid = '00000000000000000000000000000000'
        if (folderid == '00000000000000000000000000000000'):
            # 根目录信息
            folder['fid'] = '00000000000000000000000000000000'
            folder['pid'] = '00000000000000000000000000000000'
            folder['fname'] = '/'
            folder['ftype'] = 0
            folder['fsize'] = 0
            folder['fdate'] = 0
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
            logger.info('downloading - [%d/%d] pid:%s [%d, %d]' % (index, len(tasklist), pid, start, end))

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
                            logger.warning('req terminated - pid:%s' % pid)
                            r.close()
                            return None
                        except Exception as ex:
                            logger.error('%s - pid:%s' % (ex, pid))
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
                            logger.warning('req terminated - pid:%s' % pid)
                            r.close()
                            return None
                        except Exception as ex:
                            logger.error('%s - pid:%s' % (ex, pid))
                            r.close()
                            return None
            else:
                pass
            logger.info('downloaded - pid:%s' % pid)

    def doTasksFast(self, fid, tasks, writer, buff):
        # 快速下载没有完成
        logging.error('undo fast function')
        raise Exception()




# 扫描目录
def scanfiles(root):
    print('scan files')
    dirs = []
    files = []
    # 遍历目录
    for parent,dirnames,filenames in os.walk(root):
        for dirname in dirnames:
            dirs.append(os.path.join(parent, dirname))
        for filename in filenames:
            files.append(os.path.join(parent, filename))
    return dirs, files


if __name__ == '__main__':
    #
    root = './data'
    dirs, files = scanfiles(root)
    print('dirs count:', len(dirs))
    print('files count:', len(files))
    cookie = 'PHPSESSI=h32jop8mctq35573ol2mam6ts2'
    db = DatabaseClass('data.db')
    disk = YunDiskClass(cookie, db)

    num = 0
    count = len(files)
    for f in files:
        num = num + 1
        if (sys.version_info < (3, 0)): fname = f.decode('gbk')
        else: fname = f
        print('%03d/%d: %s' % (num, count, fname))
        if (fname.lower().endswith('.mp4')==False): continue
        fid = disk.UPFile(fname)

    print('OK.')

'''
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
  
# 第三方 SMTP 服务
mail_host = "smtp.126.com"          # SMTP服务器
mail_user = "dodomails"             # 用户名
mail_pass = "adodo1"                # 密码(这里的密码不是登录邮箱密码，而是授权码)
sender = 'dodomails@126.com'        # 发件人邮箱
receivers = ['18477218581@139.com'] # 接收人邮箱
receivers = ['347502077@QQ.COM', 'dodomails@126.com']   # 接收人邮箱


msg = MIMEMultipart()
msg["From"] = sender                # 发件人
msg["To"] = ";".join(receivers)     # 收件人
msg["Subject"] = '邮件标题13'       # 邮件标题
mail_context = "内容123456789"
# 邮件正文
msg.attach(MIMEText(mail_context, 'plain', 'utf-8'))
#图片附件
#不同的目录下要写全文件路径
with open('test.rar','rb') as picAtt:
    #msgImg = MIMEImage(picAtt.read())
    #msgImg.add_header('Content-Disposition', 'attachment', filename='你.jpg')
    #msgImg.add_header('Content-ID', '<0>')
    #msgImg.add_header('X-Attachment-Id', '0')
    msgImg = MIMEBase('application', 'octet-stream')
    print(msgImg)
    msgImg.set_payload(picAtt.read())
    #msg.attach(msgImg)
# 构造附件
att = MIMEText(open('test.txt', "rb").read(), "base64", "utf-8")
att["Content-Type"] = "application/octet-stream"
# 附件名称为中文时的写法
att.add_header("Content-Disposition", "attachment", filename=("utf-8", "", "测试结果.txt"))
# 附件名称非中文时的写法
# att["Content-Disposition"] = 'attachment; filename="test.html")'
msg.attach(att)

  
try:
    smtpObj = smtplib.SMTP_SSL(mail_host, 465)                  # 启用SSL发信, 端口一般是465
    smtpObj.login(mail_user, mail_pass)                         # 登录验证
    smtpObj.sendmail(sender, receivers, msg.as_string())        # 发送
    print("mail has been send successfully.")
except smtplib.SMTPException as e:
    print(e)

# ================================================
'''


'''
# 扫描目录
def scanfiles(root):
    print('scan files')
    dirs = []
    files = []
    # 遍历目录
    for parent,dirnames,filenames in os.walk(root):
        for dirname in dirnames:
            dirs.append(os.path.join(parent, dirname))
        for filename in filenames:
            files.append(os.path.join(parent, filename))
    return dirs, files

if __name__ == '__main__':
    # main
    print('[==DoDo==]')
    print('Bundle Maker.')
    print('Encode: %s' %  sys.getdefaultencoding())

    #
    dirs, files = scanfiles('./')
    print(dirs)
    print(files)
'''


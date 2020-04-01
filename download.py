# -*- coding: utf-8 -*-
import sys, os, re, requests, time, json, socket, threading, Queue, sqlite3
from threading import Thread
#socket.setdefaulttimeout(20)                    # outtime set 20s
mutex = threading.Lock()                        # 线程锁
requests.packages.urllib3.disable_warnings()    # 禁用安全请求警告


# 1. 输入AV号
# 2. 自动读取分P
# 3. 选择清晰度
# 4.

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36',
    'Referer': 'https://www.bilibili.com'
}
PROXIES = {
    #"https": "http://127.0.0.1:8087"
    #'https': 'socks5://127.0.0.1:1080'
}
TIMEOUT = 30
BLOCK_SIZE = 2 * 1024 * 1024                    # 分块大小
MAX_THREADS = 32                                # 最大线程
CACHE_DIR = u'./cache/'                         # 缓存目录
DB_FILE = u'./datas/datas.db'                   # 数据文件
SESSDATA = u'7bbc68b0%2C1600608099%2Ce0591*31'  # COOKIE


##########################################################################
class Worker(Thread):
    # thread pool, must python 2.7 up
    worker_count = 0

    def __init__(self, workQueue, resultQueue, timeout=0, **kwds):
        Thread.__init__(self, **kwds)
        self.id = Worker.worker_count
        Worker.worker_count += 1
        self.setDaemon(True)
        self.workQueue = workQueue
        self.resultQueue = resultQueue
        self.timeout = timeout
        self.start()

    def run(self):
        ''' the get-some-work, do-some-work main loop of worker threads '''
        while True:
            try:
                callable, args, kwds = self.workQueue.get(timeout=self.timeout)
                res = callable(*args, **kwds)
                # print "worker[%2d]: %s" % (self.id, str(res))
                self.resultQueue.put(res)
            except Queue.Empty:
                break
            except:
                print 'worker[%2d]' % self.id, sys.exc_info()[:2]


class WorkerPool:
    # thread pool
    def __init__(self, num_of_workers=10, timeout=1):
        self.workQueue = Queue.Queue()
        self.resultQueue = Queue.Queue()
        self.workers = []
        self.timeout = timeout
        self._recruitThreads(num_of_workers)

    def _recruitThreads(self, num_of_workers):
        for i in range(num_of_workers):
            worker = Worker(self.workQueue, self.resultQueue, self.timeout)
            self.workers.append(worker)

    def wait_for_complete(self):
        # ...then, wait for each of them to terminate:
        while len(self.workers):
            worker = self.workers.pop()
            worker.join()
            if worker.isAlive() and not self.workQueue.empty():
                self.workers.append(worker)
        # print "All jobs are are completed."

    def add_job(self, callable, *args, **kwds):
        self.workQueue.put((callable, args, kwds))

    def get_result(self, *args, **kwds):
        return self.resultQueue.get(*args, **kwds)


##########################################################################

class BilibiliClient:
    # 初始化
    def __init__(self, conn, sessdata, cachedir):
        # 登陆Bilibili
        self._conn = conn
        self._session = None
        self._session = requests.session()
        self._session.cookies.set('SESSDATA', sessdata)
        self._cachedir = cachedir
        self._count = 0
        self.HASH58 = 'fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF'

    # 获取视频细节 自动判断AVID 和 BVID
    def GetDetails(self, vid):
        # https://api.bilibili.com/x/web-interface/view?aid=70520063
        if (str(vid).lower().startswith('bv')):
            url = u'https://api.bilibili.com/x/web-interface/view?bvid=%s' % vid
        else: url = u'https://api.bilibili.com/x/web-interface/view?aid=%s' % vid
        data = self.GetJson(url)
        return data

    # 获取用户所有视频信息
    def GetSubmitVideos(self, mid, page=0, pagesize=20):
        # https://space.bilibili.com/ajax/member/getSubmitVideos?mid=11433771&page=1&pagesize=100
        # 如果PAGE等于0 递归返回所有
        videos = []
        if (page == 0): url = u'https://space.bilibili.com/ajax/member/getSubmitVideos?mid={0}&page={1}&pagesize={2}'.format(mid, 1, pagesize)
        else: url = u'https://space.bilibili.com/ajax/member/getSubmitVideos?mid={0}&page={1}&pagesize={2}'.format(mid, page, pagesize)
        #
        res = self.GetJson(url)
        if (res == None or res['status'] == False): raise Exception('fail to mid: %s' % mid)
        count = res['data']['count']
        pages = res['data']['pages']
        for item in res['data']['vlist']: videos.append(item)
        #
        if (page == 0):
            for n in range(2, (count + pagesize - 1) / pagesize + 1):
                morevideos = self.GetSubmitVideos(mid, n, pagesize)
                videos.extend(morevideos)

        return videos

    # 下载视频
    def DownloadVideos(self, aid, section=0, score=80):
        # aid: 视频AV号
        # section: 分P 等于0全下
        # score: 视频质量
        #
        # 1. 先解析视频
        # 2. 再分P下载
        # 检查是否完成 @DONE 文件
        alldonefile = u'%s/@DONE_%s' % (self._cachedir, aid)
        if (os.path.exists(alldonefile)): return
        #
        data = self.GetDetails(aid)
        title = data['title']
        for item in data['pages']:
            page = item['page']
            cid = item['cid']
            part = item['part']
            if (section>0 and page!=section): continue
            # 新建目录 下载
            outdir = u'%s/%s━%s/%s━%s/' % (self._cachedir, aid, self.StrToName(title), cid, self.StrToName(part))
            self.DownloadSection(aid, cid, score, self.StrToName(part), outdir)
            
        # 通过@DONE检查数据是否都下载好了
        success = True
        for item in data['pages']:
            page = item['page']
            cid = item['cid']
            part = item['part']
            if (section>0 and page!=section): continue
            # 新建目录 下载
            outdir = u'%s/%s━%s/%s━%s/' % (self._cachedir, aid, self.StrToName(title), cid, self.StrToName(part))
            donefile = '%s%s' % (outdir, '@DONE')
            if (os.path.exists(donefile)==False): success = False
        #
        if (success):
            f = open(alldonefile, 'wb')
            f.close()
        #
        return

    # 规范文件命名
    def StrToName(self, name):
        result = name.replace('\\', '_')
        result = result.replace('/', '_')
        result = result.replace('<', '_')
        result = result.replace('>', '_')
        result = result.replace('|', '_')
        result = result.replace('*', '_')
        result = result.replace('?', '_')
        result = result.replace(':', '_')
        result = result.replace('"', '_')
        result = result.replace(' ', '_')
        result = result.replace(u'\u273f', '_')
        # 因为windows文件名长度限制
        if (len(result) > 60): result = result[0:60]
        return result

    # 下载视频分P
    def DownloadSection(self, aid, cid, score, name, outdir):
        # aid: 视频ID
        # section: 分P
        # score: 视频质量
        # outdir: 输出路径
        if (os.path.exists(outdir) == False): os.makedirs(outdir)
        # 检查是否已完成
        if (os.path.exists(outdir+'/@DONE')): return
        # 获取下载链接 https://api.bilibili.com/x/player/playurl?cid=122175562&avid=70520063&qn=80
        url = u'https://api.bilibili.com/x/player/playurl?cid={0}&avid={1}&qn={2}'.format(cid, aid, score)
        res = self._session.get(url, headers=HEADERS)
        data = json.loads(res.text)
        if (data['code'] != 0):
            # 解析失败
            print data['message']
            sys.exit(data['code'])
        else:
            index = 0
            for item in data['data']['durl']:
                durl = item['url']
                print(u'>>>> download aid:%s cid:%s num:%d - %s' % (aid, cid, index, name))
                self.DownloadFile(durl, outdir, u'%s%d' %(name, index))
                index = index + 1
        return

    # 下载文件
    def DownloadFile(self, url, outdir, namewithoutext):
        # 下载文件
        if (os.path.exists(outdir) == False): os.makedirs(outdir)
        # 写合并脚本
        f = open(u'%s/!%s.bat' % (outdir, namewithoutext), 'wb')
        cmdline = u'copy /b "%%~dp0/%s*.block" "%%~dp0/_%s.flv"\r\n' % (namewithoutext, namewithoutext)
        cmdline += u'del /q "%~dp0/*.block"\r\n'
        cmdline += u'type nul > "%~dp0/@DONE"\r\n'
        # 格式转换
        cmdline += u'"%s/ffmpeg.exe" -y -i "%%~dp0/_%s.flv" -c copy "%%~dp0/_%s.mp4"\r\n' % (os.path.abspath(os.path.dirname(__file__)).decode('gbk'), namewithoutext, namewithoutext)
        cmdline += u'del /q "%%~dp0/_%s.flv"\r\n' % namewithoutext
        #
        f.write(cmdline.encode('gbk'))
        f.close()
        # 获取文件大小 计算要多少分块
        size = self.GetSize(url)
        self._count = size / BLOCK_SIZE + 1
        index = 0
        workers = WorkerPool(MAX_THREADS)
        # 分块下载
        for start in range(0, size, BLOCK_SIZE):
            end = start + BLOCK_SIZE
            if (end >= size): end = size
            index = index + 1
            # 多线程
            workers.add_job(self.DownloadPart, url, outdir, namewithoutext, index, start, end - 1)
            # 单线程
            # self.DownloadPart(url, outdir, namewithoutext, start, end - 1)

        workers.wait_for_complete()
        print('all thread done..................')

        # 合并
        self.UnionFile(outdir, namewithoutext, size)

    # 获取JSON
    def GetJson(self, url):
        global HEADERS
        global PROXIES
        global TIMEOUT

        # 重试3次
        for i in range(3):
            try:
                res = self._session.get(url, proxies=PROXIES, headers=HEADERS, timeout=TIMEOUT, verify=False)
                data = json.loads(res.text)
                return data
            except Exception as ex: pass
        # 错误返回空
        raise Exception('faild url: ' + url)

    # 合并文件
    def UnionFile(self, outdir, name, size):
        # 检查数据是否全部完成 如果完成 合并数据
        # 检查所有block文件数量
        success = True
        for start in range(0, size, BLOCK_SIZE):
            outfile = u'%s/%s_%012d.block' % (outdir, name, start)
            if (os.path.exists(outfile)==False): success = False
        #
        if (success):
            print('all done.')
            cmdfile = u'%s/!%s.bat' % (outdir, name)
            cmdfile = os.path.abspath(cmdfile).encode('gbk')
            os.system(cmdfile)
            print(cmdfile)
        else:
            print('error done.')

    # 下载分块
    def DownloadPart(self, url, outdir, name, index, start, end):
        # 下载分块
        try:
            outfile = u'%s/%s_%012d.block' % (outdir, name, start)
            if (os.path.exists(outfile)): return 100
            if (os.path.exists(outdir) == False): os.makedirs(outdir)
            mutex.acquire()
            print (u'[%d/%d]: %d - %d %s' % (index, self._count, start, end, name))
            mutex.release()
            # 开始下载
            data = self.DownloadData(url, start, end)
            if (data == None):
                return 100
            f = open(outfile, 'wb')
            f.write(data)
            f.close()
        except Exception as ex:
            mutex.acquire()
            print (u'error download in: %s' % ex)
            mutex.release()

    # 下载数据
    def DownloadData(self, url, start, end):
        # 下载数据
        # 下载流文件
        # url: 文件地址
        # start: 开始偏移量
        # end: 小于0返回数据到结束
        #
        global HEADERS
        global PROXIES

        res = self._session.get(url, data={}, timeout=30, headers=dict(HEADERS, **{'Range': 'bytes=%d-%d' % (start, end)}), proxies=PROXIES, stream=True, verify=False)
        if (res.status_code != 206):
            res.close()
            mutex.acquire()
            print 'error code: %s' % res.status_code
            mutex.release()
            return None
        #
        data = None
        buff = 4096
        for chunk in res.iter_content(chunk_size=buff):
            # 分块下载
            if chunk:  # filter out keep-alive new chunks
                try:
                    # writer: 输出流
                    # writer.write(chunk)
                    # writer.flush()
                    if (data == None): data = chunk
                    else: data += chunk
                # except socket.error:
                #     mutex.acquire()
                #     print('error: req terminated.......')
                #     mutex.release()
                #     res.close()
                #     return None
                except Exception as ex:
                    mutex.acquire()
                    print('error: %s.' % ex)
                    mutex.release()
                    res.close()
                    return None
        #

        #
        res.close()
        if (len(data) != end - start + 1):
            mutex.acquire()
            print('error: [%s - %s] data size diff !' % (start, end))
            mutex.release()
            return None
        #
        return data

    # 获取文件大小
    def GetSize(self, url):
        # 获取文件大小
        res = self._session.get(url, headers=dict(HEADERS, **{'Range': 'bytes=0-1'}), proxies=PROXIES, stream=True, verify=False)
        sizetext = res.headers['Content-Range']
        res.close()
        size = int(sizetext[sizetext.find('/') + 1:])
        print('size: %s' % size)
        return size

    # 检查当前用户信息
    def CheckUser(self):
        # 检查当前登陆用户信息
        url = u'https://api.bilibili.com/x/space/myinfo?jsonp=jsonp'
        res = self._session.get(url, headers=HEADERS)
        data = json.loads(res.text)
        if (data['code'] != 0):
            print data['message']
            sys.exit(data['code'])
        else:
            print(u'user: %s' % data['data']['name'])
            print(u'mid: %s' % data['data']['mid'])

    # BVID转AID
    def BVID2AID(self, bvid):
        # 算法关键字
        # 8728348608 100618342136696320 177451812 58进制 0x2084007c0
        # fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF
        r = 0
        for i, v in enumerate([11, 10, 3, 8, 4, 6]):
            r += self.HASH58.find(bvid[v]) * 58 ** i
        return (r - 0x2084007c0) ^ 0x0a93b324

    # AID转BVID
    def AID2BVID(self, aid):
        aid = (aid ^ 0x0a93b324) + 0x2084007c0
        r = list('BV1**4*1*7**')
        for v in [11, 10, 3, 8, 4, 6]:
            aid, d = divmod(aid, 58)
            r[v] = self.HASH58[d]
        return ''.join(r)



# 任务中心
class TasksServer:
    # 初始化
    def __init__(self, conn, bclient):
        #
        self._conn = conn
        self._bclient = bclient

    # 添加或更新视频作者信息
    def AddOwner(self, owner):
        mid = owner['mid']
        name = owner['name']
        face = owner['face']

        command = r'select MID from OWNER where MID=? limit 1'
        cursor = self._conn.cursor()
        cursor.execute(command, (mid,))
        record = cursor.fetchone()

        #
        if (record):
            # 更新原来的视频信息
            command = r'update OWNER set NAME=?, FACE=? where MID=?'
            args = (name, face, mid)
            cursor = self._conn.cursor()
            cursor.execute(command, args)
        else:
            # 插入新记录
            command = r'insert into OWNER(MID, NAME, FACE) values(?,?,?)'
            args = (mid, name, face)
            cursor = self._conn.cursor()
            cursor.execute(command, args)
        #
        cursor.close()
        self._conn.commit()
        return mid

    # 添加所有分P
    def AddPages(self, pages, bvid, aid=None):
        #
        cursor = self._conn.cursor()
        for item in pages:
            cid = item['cid']                   # CID
            page = item['page']                 # 当前分P
            part = item['part']                 # 分P标题
            duration = item['duration']         # 时长
            dimension = item['dimension']       # 分辨率
            #
            dimension = json.dumps(dimension)   # 分辨率

            command = r'select BVID from PAGES where BVID=? and CID=? limit 1'
            cursor.execute(command, (bvid, cid))
            record = cursor.fetchone()

            #
            if (record):
                # 更新原来的视频信息
                command = r'update PAGES set AID=?, PAGE=?, PART=?, DURATION=?, DIMENSION=? where BVID=? and CID=?'
                args = (aid, page, part, duration, dimension, bvid, cid)
                cursor.execute(command, args)
            else:
                # 插入新记录
                command = r'insert into PAGES(BVID, AID, CID, PAGE, PART, DURATION, DIMENSION) values(?,?,?,?,?,?,?)'
                args = (bvid, aid, cid, page, part, duration, dimension)
                cursor.execute(command, args)
        #
        cursor.close()
        self._conn.commit()

    # 添加视频信息
    def AddVideos(self, data):
        #
        bvid = data['bvid']                 # BVID
        aid = data['aid']                   # AID
        videos = data['videos']             # 视频数量
        tid = data['tid']                   # 视频分类
        tname = data['tname']               # 视频分类
        pic = data['pic']                   # 视频封面
        title = data['title']               # 视频标题
        pubdate = data['pubdate']           # 发布时间 秒
        ctime = data['ctime']               # 视频审核通过时间 秒
        desc = data['desc']                 # 视频简介
        state = data['state']               # 视频状态
        attribute = data['attribute']       # 视频属性
        duration = data['duration']         # 视频总时长 秒
        rights = data['rights']             # 版权相关
        owner = data['owner']               # 视频作者
        stat = data['stat']                 # 视频统计
        dynamic = data['dynamic']           # 视频动态
        cid = data['cid']                   # 首页视频的CID
        dimension = data['dimension']       # 首页视频的分辨率
        no_cache = data['no_cache']         # 无缓存
        pages = data['pages']               # 所有分段视频
        subtitle = data['subtitle']         # 联动
        mission_id = data['mission_id']     # 任务ID
        #
        mid = owner['mid']                  # 作者ID
        rights = json.dumps(rights)         # 版权
        stat = json.dumps(stat)             # 统计
        dimension = json.dumps(dimension)   # 分辨率
        subtitle = json.dumps(subtitle)     # 联动

        #
        command = r'select BVID from VIDEOS where BVID=? limit 1'
        cursor = self._conn.cursor()
        cursor.execute(command, (bvid,))
        record = cursor.fetchone()

        #
        if (record):
            # 更新原来的视频信息
            command = r'update VIDEOS set AID=?, VIDEOS=?, TID=?, TNAME=?, PIC=?, ' \
                      r'TITLE=?, PUBDATE=?, CTIME=?, DESC=?, STATE=?, ATTRIBUTE=?, ' \
                      r'DURATION=?, RIGHTS=?, OWNER=?, STAT=?, DYNAMIC=?, CID=?, ' \
                      r'DIMENSION=?, NO_CACHE=?, SUBTITLE=?, MISSION_ID=?' \
                      r'where BVID=?'
            args = (aid, videos, tid, tname, pic, title, pubdate, ctime, desc,
                    state, attribute, duration, rights, mid, stat, dynamic, cid,
                    dimension, no_cache, subtitle, mission_id, bvid)
            cursor = self._conn.cursor()
            cursor.execute(command, args)
        else:
            # 插入新记录
            command = r'insert into VIDEOS(BVID, AID, VIDEOS, TID, TNAME, PIC, ' \
                      r'TITLE, PUBDATE, CTIME, DESC, STATE, ATTRIBUTE, ' \
                      r'DURATION, RIGHTS, OWNER, STAT, DYNAMIC, CID, ' \
                      r'DIMENSION, NO_CACHE, SUBTITLE, MISSION_ID) ' \
                      r'values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
            args = (bvid, aid, videos, tid, tname, pic, title, pubdate, ctime, desc,
                    state, attribute, duration, rights, mid, stat, dynamic, cid,
                    dimension, no_cache, subtitle, mission_id)
            cursor = self._conn.cursor()
            cursor.execute(command, args)
        #
        self._conn.commit()
        cursor.close()

        #

    # 添加任务
    def AddTask(self, vid):
        #
        res = self._bclient.GetDetails(vid)
        # 查询任务列表是否有
        if (res['code']!=0): raise Exception(res['message'])
        data = res['data']
        bvid = data['bvid']
        aid = data['aid']
        owner = data['owner']
        pages = data['pages']


        # 1. 存储视频作者信息
        self.AddOwner(owner)
        # 2. 解析所有分P 加入任务列表
        self.AddPages(pages, bvid, aid)
        # 3. 现将信息存档
        self.AddVideos(data)
        pass


    # 获取任务列表
    def TasksList(self):
        pass

    def SetCookie(self, cookie):
        pass

class DownloadServer:
    # 初始化
    def __init__(self, conn):
        pass

    # 循环检查任务
    def CheckTasks(self):
        #
        pass





def main():
    # 程序入口
    print('[==DoDo==]')
    print('Bilibile Download.')
    print('Encode: %s' %  sys.getdefaultencoding())
    print('APP ID: %s' % os.getpid())
    print('===================================================')
    #
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    bclient = BilibiliClient(conn, SESSDATA, CACHE_DIR)
    bclient.CheckUser()
    # print bclient.BVID2AID('BV17x411w7KC')
    # print bclient.AID2BVID(170001)

    res = bclient.GetDetails('BV1U7411t7sG')
    print res

    taskServer = TasksServer(conn, bclient)
    taskServer.AddTask('BV1U7411t7sG')

    print 'done.'



if __name__ == '__main__':
    #

    #
    # cachedir = u'./data_dance/'
    # #
    # bclient = BilibiliClient(cachedir)
    # bclient.CheckUser()



    print(divmod(123, 60))

    #
    # size = bclient.GetSize('https://www.runoob.com/try/demo_source/movie.mp4')
    # bclient.DownloadFile('http://upos-hz-mirrorcosu.acgvideo.com/upgcxcode/57/57/53055757/53055757_da2-1-80.flv?e=ig8euxZM2rNcNbhj7zUVhoMz7buBhwdEto8g5X10ugNcXBlqNxHxNEVE5XREto8KqJZHUa6m5J0SqE85tZvEuENvNo8g2ENvNo8i8o859r1qXg8xNEVE5XREto8GuFGv2U7SuxI72X6fTr859r1qXg8gNEVE5XREto8z5JZC2X2gkX5L5F1eTX1jkXlsTXHeux_f2o859IB_&uipk=5&nbs=1&deadline=1570768436&gen=playurl&os=cosu&oi=1866155180&trid=2142ab14370a431fbdcb8caf14e819aau&platform=pc&upsig=b50c5ae19b513ca9620ad61defe44f25&uparams=e,uipk,nbs,deadline,gen,os,oi,trid,platform&mid=955723', basedir, 'test')
    # data = bclient.GetDetails(30406774)
    # data = bclient.GetDetails(70520063)
    #

    # f = open('list.txt', 'rb')
    # text = f.read()
    # f.close()
    # text = text.replace('\r', '')
    # for line in text.split('\n'):
    #     print(line)
    #     if (line.strip() == ''): continue
    #     bclient.DownloadVideos(line)
        
    # aid = '68668952'
    # bclient.DownloadVideos(aid)

    # res = bclient.GetSubmitVideos(11433771, 0, 25)

    main()
    #
    print('OK.')
    

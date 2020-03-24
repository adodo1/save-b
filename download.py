# -*- coding: utf-8 -*-
import sys, os, re, requests, time, json, socket, threading, Queue
from threading import Thread
#socket.setdefaulttimeout(20)                    # outtime set 20s
mutex = threading.Lock()                        # 线程锁
requests.packages.urllib3.disable_warnings()    # 禁用安全请求警告


# 1. 输入AV号
# 2. 自动读取分P
# 3. 选择清晰度
# 4.

SESSDATA = '2fdc0032%2C1573118311%2Cfd531ea1'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36',
    'Referer': 'https://www.bilibili.com'
}
PROXIES = {
    #"https": "http://127.0.0.1:8087"
    #'https': 'socks5://127.0.0.1:1080'
}
BLOCK_SIZE = 2 * 1024 * 1024            # 分块大小
MAX_THREADS = 32                        # 最大线程


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
    def __init__(self, basedir=u'./data/'):
        # 登陆Bilibili
        if (os.path.exists('SESSDATA.txt')):
            SESSDATA = open('SESSDATA.txt', 'rb').read()
        self._session = None
        self._session = requests.session()
        self._session.cookies.set('SESSDATA', SESSDATA)
        self._basedir = basedir
        self._count = 0

    # 获取视频细节
    def GetDetails(self, aid):
        #
        # https://api.bilibili.com/x/web-interface/view?aid=70520063
        url = u'https://api.bilibili.com/x/web-interface/view?aid=%s' % aid
        res = self._session.get(url, headers=HEADERS)
        data = json.loads(res.text)
        if (data['code'] != 0):
            # 解析失败
            print data['message']
            sys.exit(data['code'])
        else:
            # 解析成功
            result = data['data']
            print(u'aid: %s' % data['data']['aid'])
            print(u'title: %s' % data['data']['title'])
            print(u'count: %s' % data['data']['videos'])
            print(u'===================================================')
            for page in data['data']['pages']:
                index = page['page']
                duration = '%03d:%02d' % divmod(page['duration'], 60)
                cid = page['cid']
                vname = page['part']
                print(u'%03d: %s - %s - %s' % (index, duration, cid, vname))
            return result

    # 下载视频
    def DownloadVideos(self, aid, section=0, score=80):
        # aid: 视频AV号
        # section: 分P 等于0全下
        # score: 视频质量
        #
        # 1. 先解析视频
        # 2. 再分P下载
        # 检查是否完成 @DONE 文件
        alldonefile = u'%s/@DONE_%s' % (self._basedir, aid)
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
            outdir = u'%s/%s━%s/%s━%s/' % (self._basedir, aid, self.StrToName(title), cid, self.StrToName(part))
            self.DownloadSection(aid, cid, score, self.StrToName(part), outdir)
            
        # 通过@DONE检查数据是否都下载好了
        success = True
        for item in data['pages']:
            page = item['page']
            cid = item['cid']
            part = item['part']
            if (section>0 and page!=section): continue
            # 新建目录 下载
            outdir = u'%s/%s━%s/%s━%s/' % (self._basedir, aid, self.StrToName(title), cid, self.StrToName(part))
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

    def GetSize(self, url):
        # 获取文件大小
        res = self._session.get(url, headers=dict(HEADERS, **{'Range': 'bytes=0-1'}), proxies=PROXIES, stream=True, verify=False)
        sizetext = res.headers['Content-Range']
        res.close()
        size = int(sizetext[sizetext.find('/') + 1:])
        print('size: %s' % size)
        return size

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



if __name__ == '__main__':
    #
    print('[==DoDo==]')
    print('Bilibile Download.')
    print('Encode: %s' %  sys.getdefaultencoding())
    print('APP ID: %s' % os.getpid())
    print('===================================================')
    #
    basedir = u'./data_dance/'
    #
    bclient = BilibiliClient(basedir)
    bclient.CheckUser()
    #
    # size = bclient.GetSigze('https://www.runoob.com/try/demo_source/movie.mp4')
    # bclient.DownloadFile('http://upos-hz-mirrorcosu.acgvideo.com/upgcxcode/57/57/53055757/53055757_da2-1-80.flv?e=ig8euxZM2rNcNbhj7zUVhoMz7buBhwdEto8g5X10ugNcXBlqNxHxNEVE5XREto8KqJZHUa6m5J0SqE85tZvEuENvNo8g2ENvNo8i8o859r1qXg8xNEVE5XREto8GuFGv2U7SuxI72X6fTr859r1qXg8gNEVE5XREto8z5JZC2X2gkX5L5F1eTX1jkXlsTXHeux_f2o859IB_&uipk=5&nbs=1&deadline=1570768436&gen=playurl&os=cosu&oi=1866155180&trid=2142ab14370a431fbdcb8caf14e819aau&platform=pc&upsig=b50c5ae19b513ca9620ad61defe44f25&uparams=e,uipk,nbs,deadline,gen,os,oi,trid,platform&mid=955723', basedir, 'test')
    # data = bclient.GetDetails(30406774)
    # data = bclient.GetDetails(70520063)
    #

    f = open('list.txt', 'rb')
    text = f.read()
    f.close()
    text = text.replace('\r', '')
    for line in text.split('\n'):
        print(line)
        if (line.strip() == ''): continue
        bclient.DownloadVideos(line)
        
    #aid = '68668952'
    #bclient.DownloadVideos(aid)
    #
    print('OK.')
    

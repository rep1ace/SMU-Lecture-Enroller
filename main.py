import json
import time
from datetime import datetime, date, timedelta, timezone
from io import BytesIO
from wsgiref.util import request_uri

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image
from bs4 import BeautifulSoup
import re
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import asyncio
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

captcha_url = "https://zhjw.smu.edu.cn/yzm?d="
login_url = "https://zhjw.smu.edu.cn/new/login"
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Host": "zhjw.smu.edu.cn",
    "Referer": "https://zhjw.smu.edu.cn/",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def encrypt_password(password, verifycode):
    key = (verifycode * 4).encode('utf-8')
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(password.encode('utf-8'), AES.block_size))
    return encrypted.hex()


def get_captcha(session):
    captcha_response = session.get(captcha_url + str(int(time.time() * 1000)), headers=headers)
    img = Image.open(BytesIO(captcha_response.content))
    img.show()
    captcha = input("请输入验证码: ")
    return captcha


def login(account, password, captcha, session):
    encrypted_password = encrypt_password(password, captcha)
    data = {
        "account": account,
        "pwd": encrypted_password,
        "verifycode": captcha
    }
    response = session.post(login_url, data=data, headers=headers)
    if response.status_code == 200 and "成功" in response.text:
        print("登录成功")
        print(response.url)
        return response
    else:
        print("登录失败，原因：", response.text)
        return 1


def calibration(response):
    server_dt_utc = parsedate_to_datetime(response.headers['Date']).astimezone(timezone.utc)
    rtt_half = response.elapsed / 2
    local_recv_utc = datetime.now(timezone.utc)
    diff = (server_dt_utc + rtt_half) - local_recv_utc
    print(f"Servertime(UTC header): {server_dt_utc}")
    print(f"RTT/2: {rtt_half.total_seconds():.6f}s")
    print(f"时间差值(秒，服务器-本地)：{diff.total_seconds():.6f}")
    print(f"currtime Asia/Shanghai: {datetime.now(ZoneInfo('Asia/Shanghai'))}")

    global time_diff
    time_diff = diff
    return diff


def get_course_category(session):
    session.get("https://zhjw.smu.edu.cn/new/welcome.page", headers=headers)
    url = "https://zhjw.smu.edu.cn/new/student/xsxk/"
    response = session.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'lxml')
    courses = soup.find_all('div', attrs={'data-href': True})
    print("选课类型：")
    coursedict = {}
    for idx, course_category in enumerate(courses, start=1):
        course_title = course_category.attrs.get("lay-iframe")
        course_link = course_category.attrs.get("data-href")
        xklxdm = re.search(r"\d\d",course_link)
        coursedict[idx] = xklxdm.group(0)
        print(f"{idx}. {course_title}")
    return coursedict


def get_course_list(session, coursecateurl):
    courselisturl = coursecateurl + "/kxkc"
    payload = {
        'page': 1,
        'rows': 50,
        'sort': 'kcrwdm',
        'order': 'asc'
    }
    response = session.post(courselisturl, headers=headers, data=payload)
    responsetext = json.loads(response.text)
    total = responsetext['total']
    courses = responsetext['rows']
    while len(courses) < total:
        payload['page'] += 1
        response = session.post(courselisturl, headers=headers, data=payload)
        temp = json.loads(response.text)['rows']
        for course in temp:
            courses.append(course)
    for i in range(len(courses)):
        print(f"{i + 1}. {courses[i]['kcmc']} {courses[i]['teaxm']}")
    return courses, coursecateurl


def select_job(order1, order2, session, courses, coursecateurl, loop, done_evt: asyncio.Event):
    """
    到点后执行的选课任务：最多尝试 20 次，
    第一次失败会切换到 order2，再失败则打印“什么都没抢到”。
    """
    try:
        order = order1
        flag = False

        for i in range(20):
            resp = order_course(session,
                                courses[order - 1]['kcrwdm'],
                                courses[order - 1]['kcmc'],
                                coursecateurl)
            try:
                resptext = json.loads(resp.text)
            except Exception:
                time.sleep(0.1)
                continue

            code = resptext.get('code')
            msg = resptext.get('message', '')

            if code == 0 or msg == '您已经选了该门课程':
                print('选课成功')
                return
            elif msg == '超出选课要求门数(1.0门)':
                print('大妈你逗我呢，你已经选过课了')
                return
            elif code == -1 and not flag:
                # 第一次失败，切换到备选
                order = order2
                flag = True
            elif code == -1 and flag:
                print('什么都没抢到')
                return
            time.sleep(0.1)
    finally:
        loop.call_soon_threadsafe(done_evt.set)



def order_course(session, kcrwdm, kcmc, url):
    url = url + '/add'
    payload = {
        'kcrwdm': kcrwdm,
        'kcmc': kcmc,
        'qz': -1,
        'xxyqdm':'',
        'hlct': 0
    }
    response = session.post(url, headers=headers, data=payload)
    print(response.text)
    return response


async def main():
    account = input("请输入账号")
    password = input("请输入密码")
    session = requests.Session()
    captcha = get_captcha(session)
    response = login(account, password, captcha, session)
    time_diff = calibration(response)
    coursedict = get_course_category(session)
    courses, coursecateurl = get_course_list(session, "https://zhjw.smu.edu.cn/new/student/xsxk/xklx/" + coursedict[int((input("请填写序号")))])
    order1 = int(input('请输入第一志愿课程对应序号'))
    order2 = int(input('请输入第二志愿课程对应序号'))
    selection_time_str = input('请输入抢课时间。格式为HH:MM:SS 例：13:00:00；08:08:00，注意是英文冒号')
    selection_time = datetime.strptime(selection_time_str, '%H:%M:%S').time()
    selection_dt_local = datetime.combine(date.today(), selection_time)
    run_at_server = selection_dt_local - time_diff
    if run_at_server <= datetime.now():
        run_at_server = datetime.now() + timedelta(seconds=0.1)
    loop = asyncio.get_running_loop()
    done_evt = asyncio.Event()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        select_job,
        trigger=DateTrigger(run_date=run_at_server),
        args=[order1, order2, session, courses, coursecateurl, loop, done_evt],
        id='course_selection_once',
        misfire_grace_time=1,
        coalesce=True  # 若多次合并，只执行一次
    )
    scheduler.start()
    print(f'已计划在本地时间 {run_at_server.strftime("%Y-%m-%d %H:%M:%S")} 选课（校正后的服务器时钟）。')
    await done_evt.wait()
    scheduler.shutdown(wait=False)

if __name__ == "__main__":

    asyncio.run(main())

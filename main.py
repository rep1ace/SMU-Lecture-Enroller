import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import md5
from io import BytesIO
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from bs4 import BeautifulSoup
from PIL import Image

ZHJW_BASE_URL = "https://zhjw.smu.edu.cn"
SSO_LOGIN_PAGE_URL = (
    "https://uis.smu.edu.cn/login.jsp"
    "?redirect=https%3A%2F%2Fzhjw.smu.edu.cn%2Fnew%2FssoLogin"
)
SSO_CAPTCHA_URL = "https://uis.smu.edu.cn/imageServlet.do"
SSO_LOGIN_URL = "https://uis.smu.edu.cn/login/login.do"
SSO_REDIRECT_URL = f"{ZHJW_BASE_URL}/new/ssoLogin"
REQUEST_TIMEOUT = (10, 30)
REQUEST_RETRY_COUNT = 3

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Host": "zhjw.smu.edu.cn",
    "Referer": "https://zhjw.smu.edu.cn/",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}


def request_with_retry(session, method, url, **kwargs):
    timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
    last_error = None
    for attempt in range(1, REQUEST_RETRY_COUNT + 1):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            print(
                f"请求失败 ({attempt}/{REQUEST_RETRY_COUNT}): "
                f"{method.upper()} {url} -> {exc}"
            )
    raise RuntimeError(
        f"请求失败，已达到最大重试次数: {method.upper()} {url} -> {last_error}"
    )


def get_captcha(session):
    page_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Host": "uis.smu.edu.cn",
        "Referer": "https://uis.smu.edu.cn/",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": headers["User-Agent"],
    }
    captcha_headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Host": "uis.smu.edu.cn",
        "Referer": SSO_LOGIN_PAGE_URL,
        "User-Agent": headers["User-Agent"],
    }

    request_with_retry(session, "get", SSO_LOGIN_PAGE_URL, headers=page_headers)
    captcha_response = request_with_retry(
        session, "get", SSO_CAPTCHA_URL, headers=captcha_headers
    )

    image = Image.open(BytesIO(captcha_response.content))
    image.show()
    return input("请输入验证码: ").strip()


def login(account, password, captcha, session):
    login_headers = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Host": "uis.smu.edu.cn",
        "Origin": "https://uis.smu.edu.cn",
        "Referer": SSO_LOGIN_PAGE_URL,
        "User-Agent": headers["User-Agent"],
        "X-KL-kis-Ajax-Request": "Ajax_Request",
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {
        "loginName": account,
        "password": md5(password.encode("utf-8")).hexdigest(),
        "randcodekey": captcha,
        "locationBrowser": "谷歌浏览器[Chrome]",
        "appid": "3550176",
        "redirect": SSO_REDIRECT_URL,
        "strength": 3,
    }

    response = request_with_retry(
        session, "post", SSO_LOGIN_URL, data=data, headers=login_headers
    )
    try:
        payload = response.json()
    except ValueError:
        print("登录失败，返回内容无法解析:", response.text)
        return None

    if "成功" in response.text and payload.get("ticket"):
        print("统一认证登录成功")
        return payload["ticket"]

    print("登录失败，原因:", response.text)
    return None


def redirect_login(session, ticket):
    return request_with_retry(
        session,
        "get",
        SSO_REDIRECT_URL,
        headers=headers,
        params={"ticket": ticket},
    )


def calibration(response):
    server_dt_utc = parsedate_to_datetime(response.headers["Date"]).astimezone(
        timezone.utc
    )
    rtt_half = response.elapsed / 2
    local_recv_utc = datetime.now(timezone.utc)
    diff = (server_dt_utc + rtt_half) - local_recv_utc
    print(f"Servertime(UTC header): {server_dt_utc}")
    print(f"RTT/2: {rtt_half.total_seconds():.6f}s")
    print(f"时间差(服务器 - 本地): {diff.total_seconds():.6f}s")
    print(f"当前上海时间: {datetime.now(ZoneInfo('Asia/Shanghai'))}")

    global time_diff
    time_diff = diff
    return diff


def get_course_category(session):
    request_with_retry(session, "get", f"{ZHJW_BASE_URL}/new/welcome.page", headers=headers)
    response = request_with_retry(
        session, "get", f"{ZHJW_BASE_URL}/new/student/xsxk/", headers=headers
    )
    soup = BeautifulSoup(response.content, "lxml")
    courses = soup.find_all("div", attrs={"data-href": True})
    print("选课类型:")
    course_dict = {}
    for idx, course_category in enumerate(courses, start=1):
        course_title = course_category.attrs.get("lay-iframe")
        course_link = course_category.attrs.get("data-href", "")
        xklxdm = re.search(r"\d\d", course_link)
        if not xklxdm:
            continue
        course_dict[idx] = xklxdm.group(0)
        print(f"{idx}. {course_title}")
    return course_dict


def get_course_list(session, course_category_url):
    course_list_url = f"{course_category_url}/kxkc"
    payload = {
        "page": 1,
        "rows": 50,
        "sort": "kcrwdm",
        "order": "asc",
    }
    response = request_with_retry(
        session, "post", course_list_url, headers=headers, data=payload
    )
    response_text = response.json()
    total = response_text["total"]
    courses = response_text["rows"]
    while len(courses) < total:
        payload["page"] += 1
        response = request_with_retry(
            session, "post", course_list_url, headers=headers, data=payload
        )
        courses.extend(response.json()["rows"])
    for index, course in enumerate(courses, start=1):
        print(f"{index}. {course['kcmc']} {course['teaxm']}")
    return courses, course_category_url


def select_job(order1, order2, session, courses, course_category_url, loop, done_evt):
    try:
        order = order1
        switched = False

        for _ in range(20):
            resp = order_course(
                session,
                courses[order - 1]["kcrwdm"],
                courses[order - 1]["kcmc"],
                course_category_url,
            )
            try:
                resp_text = resp.json()
            except ValueError:
                time.sleep(0.1)
                continue

            code = resp_text.get("code")
            message = resp_text.get("message", "")

            if code == 0 or message == "您已经选了这门课程":
                print("选课成功")
                return
            if message == "超出选课要求门数(1.0门)":
                print("你已经选过这门课了")
                return
            if code == -1 and not switched:
                order = order2
                switched = True
            elif code == -1 and switched:
                print("两个志愿都没抢到")
                return
            time.sleep(0.1)
    finally:
        loop.call_soon_threadsafe(done_evt.set)


def order_course(session, kcrwdm, kcmc, url):
    payload = {
        "kcrwdm": kcrwdm,
        "kcmc": kcmc,
        "qz": -1,
        "xxyqdm": "",
        "hlct": 0,
    }
    response = request_with_retry(
        session, "post", f"{url}/add", headers=headers, data=payload
    )
    print(response.text)
    return response


async def main():
    account = input("请输入账号: ").strip()
    password = input("请输入密码: ").strip()

    session = None
    login_response = None
    for attempt in range(1, 6):
        session = requests.Session()
        captcha = get_captcha(session)
        ticket = login(account, password, captcha, session)
        if ticket:
            login_response = redirect_login(session, ticket)
            break
        print(f"第 {attempt} 次登录失败，准备重试。")

    if session is None or login_response is None:
        raise RuntimeError("登录失败，已达到最大重试次数")

    time_diff = calibration(login_response)
    course_dict = get_course_category(session)
    category_index = int(input("请输入选课类型序号: ").strip())
    courses, course_category_url = get_course_list(
        session,
        f"{ZHJW_BASE_URL}/new/student/xsxk/xklx/{course_dict[category_index]}",
    )
    order1 = int(input("请输入第一志愿课程对应序号: ").strip())
    order2 = int(input("请输入第二志愿课程对应序号: ").strip())
    selection_time_str = input(
        "请输入抢课时间，格式 HH:MM:SS，例如 13:00:00: "
    ).strip()
    selection_time = datetime.strptime(selection_time_str, "%H:%M:%S").time()
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
        args=[order1, order2, session, courses, course_category_url, loop, done_evt],
        id="course_selection_once",
        misfire_grace_time=1,
        coalesce=True,
    )
    scheduler.start()
    print(
        "已计划在本地时间 "
        f"{run_at_server.strftime('%Y-%m-%d %H:%M:%S')} 执行选课"
    )
    await done_evt.wait()
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())

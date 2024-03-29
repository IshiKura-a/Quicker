import datetime
import os
import random
import sys
import ddddocr
import base64
import traceback
import requests
import json
import re
import time
import hashlib
import argparse
import logging
import cv2

import numpy as np

from io import BytesIO
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent
from dateutil import tz
from Crypto.Cipher import AES

parser = argparse.ArgumentParser()
parser.add_argument('--input', type=str)
parser.add_argument('--mode', choices=['interval', 'once', 'debug'])
parser.add_argument('--start_time',
                    default=datetime.datetime.now(tz.gettz('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                    )
args = parser.parse_args()
schedule = BlockingScheduler()
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
handlers = [logging.StreamHandler()]
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)
logger = logging.getLogger()


class Reserver:
    def __init__(self):
        with open(args.input, 'r', encoding='UTF-8') as f:
            # 请输入场地编号(玉泉羽毛球场(39)):
            self.venue_site_id = f.readline().replace('\n', '')
            # 请输入预约日期(yyyy-mm-dd):
            self.date = f.readline().replace('\n', '')
            # 请输入候选开始时间(hh:mm)，空格隔开:
            candidate_str = f.readline().replace('\n', '')
            self.candidate = [self.date + " " + i for i in candidate_str.split(" ")]
            # 请输入预约场数(1/2):
            self.n_site = f.readline().replace('\n', '')
            # 请输入同伴姓名（必须一起预约过）:
            companion_str = f.readline().replace('\n', '')
            self.companion = companion_str.split(" ")
            # 请输入手机号:
            self.phone = f.readline().replace('\n', '')

        logger.info("-----------------")
        logger.info("场地编号: " + str(self.venue_site_id))
        logger.info("预约日期: " + str(self.date))
        logger.info("候选时间: " + str(self.candidate))
        logger.info("预约场数: " + str(self.n_site))
        logger.info("同伴: " + str(self.companion))
        logger.info("手机号: " + self.phone)
        logger.info("-----------------\n")


class User(object):
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.login_url = "https://zjuam.zju.edu.cn/cas/login?service=http://www.tyys.zju.edu.cn/venue-server/sso/manageLogin"
        self.info_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/day/info"
        self.order_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/order/info"
        self.submit_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/order/submit"
        self.pay_url = "http://www.tyys.zju.edu.cn/venue-server/api/venue/finances/order/pay"
        self.buddy_no_url = "http://www.tyys.zju.edu.cn/venue-server/api/vip/view/buddy_no"
        self.get_captcha_url = "http://www.tyys.zju.edu.cn/venue-server/api/captcha/get"
        self.check_captcha_url = "http://www.tyys.zju.edu.cn/venue-server/api/captcha/check"
        self.sess = requests.Session()
        self.sign = ""
        self.access_token = ""
        self.deny_list = []
        self.local_storage = {}

    def login(self):
        """Login to ZJU platform"""
        res = self.sess.get(self.login_url)
        try:
            execution = re.search(
                'name="execution" value="(.*?)"', res.text).group(1)
        except BaseException as exception:
            logger.critical(res.text)
            raise exception
        res = self.sess.get(
            url='https://zjuam.zju.edu.cn/cas/v2/getPubKey').json()
        n, e = res['modulus'], res['exponent']
        encrypt_password = self._rsa_encrypt(self.password, e, n)

        data = {
            'username': self.username,
            'password': encrypt_password,
            'execution': execution,
            '_eventId': 'submit'
        }
        res = self.sess.post(url=self.login_url, data=data)

        # check if login successfully
        if '统一身份认证' in res.content.decode():
            raise LoginError('登录失败，请核实账号密码重新登录')

        timestamp = self.get_timestamp()
        self.sign = self.get_sign(path="/api/login", timestamp=timestamp, params={})
        sso_token = self.sess.cookies.get("sso_zju_tyb_token")
        res = self.sess.post("http://www.tyys.zju.edu.cn/venue-server/api/login",
                             headers={
                                 "accept": "application/json, text/plain, */*",
                                 "accept-language": "zh-CN,zh;q=0.9",
                                 "app-key": "8fceb735082b5a529312040b58ea780b",
                                 "content-type": "application/x-www-form-urlencoded",
                                 "sign": self.sign,
                                 "sso-token": sso_token,
                                 "timestamp": timestamp
                             })
        self.access_token = res.json()["data"]["token"]["access_token"]

        timestamp = self.get_timestamp()
        self.sign = self.get_sign(path="/roleLogin", timestamp=timestamp, params={})
        res = self.sess.post("http://www.tyys.zju.edu.cn/venue-server/roleLogin",
                             headers={
                                 "accept": "application/json, text/plain, */*",
                                 "accept-language": "zh-CN,zh;q=0.9",
                                 "app-key": "8fceb735082b5a529312040b58ea780b",
                                 "cgauthorization": self.access_token,
                                 "content-type": "application/x-www-form-urlencoded",
                                 "sign": self.sign,
                                 "timestamp": timestamp
                             },
                             params={
                                 "roleid": "3"
                             })
        self.access_token = res.json()["data"]["token"]["access_token"]
        if self.access_token != "":
            logger.info(self.username + " Login Success!")
        return self.sess

    def _rsa_encrypt(self, password_str, e_str, M_str):
        password_bytes = bytes(password_str, 'ascii')
        password_int = int.from_bytes(password_bytes, 'big')
        e_int = int(e_str, 16)
        M_int = int(M_str, 16)
        result_int = pow(password_int, e_int, M_int)
        return hex(result_int)[2:].rjust(128, '0')

    def get_info(self, venue_site_id, search_date):
        timestamp = self.get_timestamp()
        params = {
            "nocache": timestamp,
            "venueSiteId": venue_site_id,
            "searchDate": search_date,
        }
        url = self.info_url + "?venueSiteId=" + str(
            venue_site_id) + "&searchDate=" + search_date + "&nocache=" + timestamp

        self.sign = self.get_sign(timestamp=timestamp, params=params, path="/api/reservation/day/info")
        res = self.sess.get(url, headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "app-key": "8fceb735082b5a529312040b58ea780b",
            "cgauthorization": self.access_token,
            "content-type": "application/x-www-form-urlencoded",
            "sign": self.sign,
            "timestamp": timestamp
        })
        return res.json()

    def get_sign(self, timestamp, path, params):
        I = "c640ca392cd45fb3a55b00a63a86c618"
        c = I + path
        for key, value in sorted(params.items()):
            c += key + str(value)
        c += timestamp + " " + I
        return hashlib.md5(c.encode(encoding='UTF-8')).hexdigest()

    @staticmethod
    def choose_space(info, reserver):
        for space in info:
            for key, value in space.items():
                if key.isnumeric() and value["reservationStatus"] == 1 and value["startDate"] in reserver.candidate:
                    if reserver.n_site == 2:
                        if str(int(key) + 1) in space.keys() and space[key + 1]["reservationStatus"] == 1:
                            return [{
                                "spaceId": str(space["id"]), "timeId": str(int(key) + 1), "venueSpaceGroupId": None
                            }, {
                                "spaceId": str(space["id"]), "timeId": str(key), "venueSpaceGroupId": None
                            }]
                    else:
                        return [{
                            "spaceId": str(space["id"]), "timeId": str(key), "venueSpaceGroupId": None
                        }]
        return []

    def order(self, buddy_no, reserver):
        while True:
            response = self.get_info(reserver.venue_site_id, reserver.date)
            if str(response['code']) != '200':
                return response
            info = response["data"]["reservationDateSpaceInfo"][
                reserver.date]
            token = response["data"]["token"]

            while True:
                order = self.choose_space(info, reserver)
                if len(order) == 0:
                    logger.critical("所有场次均被预约")
                    return None

                timestamp = self.get_timestamp()
                order = str(order).replace(": ", ":").replace(", ", ",").replace("\'", "\"").replace("None", "null")
                params = {
                    "venueSiteId": reserver.venue_site_id,
                    "reservationDate": reserver.date,
                    "weekStartDate": reserver.date,
                    "reservationOrderJson": order,
                    "token": token,
                }
                self.sign = self.get_sign(path="/api/reservation/order/info", timestamp=timestamp, params=params)
                res = self.sess.post(self.order_url, headers={
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "zh-CN,zh;q=0.9",
                    "app-key": "8fceb735082b5a529312040b58ea780b",
                    "cgauthorization": self.access_token,
                    "content-type": "application/x-www-form-urlencoded",
                    "sign": self.sign,
                    "timestamp": timestamp
                }, params=params).json()

                if res["code"] == 200:
                    logger.info(order)
                    break

            buddy_list = res["data"]["buddyList"]

            buddy_ids = ""
            for buddy in sorted(buddy_list, key=lambda i: i['id']):
                if buddy["name"] in reserver.companion:
                    if len(buddy_ids) != 0:
                        buddy_ids += ","
                    buddy_ids += str(buddy["id"])

            captcha_verification = self.solve_captcha(mode='clickWord')

            # 防止：{'code': 250, 'message': '预约步骤流程耗时异常，订单提交失败', 'data': None}
            time.sleep(1)
            params = {
                "venueSiteId": reserver.venue_site_id,
                "reservationDate": reserver.date,
                "reservationOrderJson": order,
                "phone": int(reserver.phone),
                "buddyIds": buddy_ids,
                "weekStartDate": reserver.date,
                "isCheckBuddyNo": 1,
                "captchaVerification": captcha_verification,
                "buddyNo": buddy_no,
                "isOfflineTicket": 1,
                "token": token,
            }
            timestamp = self.get_timestamp()
            self.sign = self.get_sign(timestamp, "/api/reservation/order/submit", params)
            res = self.sess.post(self.submit_url, headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "app-key": "8fceb735082b5a529312040b58ea780b",
                "cgauthorization": self.access_token,
                "content-type": "application/x-www-form-urlencoded",
                "sign": self.sign,
                "timestamp": timestamp
            }, params=params).json()

            if res["code"] == 200:
                break
            else:
                logger.info(res)

        trade_no = res["data"]["orderInfo"]["tradeNo"]
        params = {
            "venueTradeNo": trade_no,
            "isApp": 0
        }
        timestamp = self.get_timestamp()
        self.sign = self.get_sign(timestamp, "/api/venue/finances/order/pay", params)
        res = self.sess.post(self.pay_url, headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "app-key": "8fceb735082b5a529312040b58ea780b",
            "cgauthorization": self.access_token,
            "content-type": "application/x-www-form-urlencoded",
            "sign": self.sign,
            "timestamp": timestamp
        }, params=params).json()

        return res

    def exec(self, buddy_no, reserver, mode):
        try:
            self.sess = requests.Session()
            self.login()
            result = self.order(buddy_no, reserver)
            if result is not None:
                logger.info(result)
                if result["code"] == 200:
                    logger.info('Success!')
                return result
            else:
                return {'code': '409'}

        except BaseException:
            logger.info(traceback.format_exc())
            return {'code': '400'}

    def get_timestamp(self):
        return str(int(round(time.time() * 1000)))

    def get_buddy_no(self):
        self.login()
        timestamp = self.get_timestamp()
        params = {}
        self.sign = self.get_sign(timestamp=timestamp, params=params, path="/api/vip/view/buddy_no")
        res = self.sess.post(self.buddy_no_url, headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "app-key": "8fceb735082b5a529312040b58ea780b",
            "cgauthorization": self.access_token,
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
            "sign": self.sign,
            "timestamp": timestamp
        })
        return res.json()['data']

    @staticmethod
    def ocr_captcha(base64_img, word_list):
        det = ddddocr.DdddOcr(det=True)
        ocr = ddddocr.DdddOcr(beta=True)

        img = base64.b64decode(base64_img)
        stream = BytesIO(img)
        image_bytes = stream.read()

        poses = det.detection(image_bytes)

        arr = np.frombuffer(img, np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        decode_dict = {}
        for box in poses:
            x1, y1, x2, y2 = box
            cropped_img = im[y1:y2, x1:x2]
            cv2.imwrite("cropped.jpg", cropped_img)
            with open("cropped.jpg", 'rb') as f:
                cropped_img = f.read()
            res = ocr.classification(cropped_img)
            decode_dict[res] = {
                'x': int((x1 + x2) / 2),
                'y': int((y1 + y2) / 2),
            }

        res = []
        for word in word_list:
            if word in decode_dict.keys():
                res.append(decode_dict[word])
            else:
                candidates = list(filter(lambda x: x not in word_list, decode_dict.keys()))
                # 碰运气
                res.append(decode_dict[random.choice(candidates)])
        return res

    def solve_click_word(self):
        if 'slider' not in self.local_storage.keys():
            # set uuid
            t = '0123456789abcdef'
            e = []
            for i in range(36):
                e.append(t[int(np.floor(16 * np.random.rand()))])
            e[14] = '4'
            e[19] = t[3 & int(e[19], 16) | 8]
            e[8] = e[13] = e[18] = e[23] = '-'
            self.local_storage['slider'] = 'slider-' + ''.join(e)
            self.local_storage['point'] = 'point-' + ''.join(e)
        client_uid = self.local_storage['point']

        timestamp = self.get_timestamp()

        url = f'{self.get_captcha_url}?captchaType=clickWord&clientUid={client_uid}&ts={timestamp}&nocache={timestamp}'
        params = {
            'captchaType': 'clickWord',
            'clientUid': client_uid,
            'ts': timestamp,
            'nocache': timestamp
        }
        self.sign = self.get_sign(timestamp=timestamp, params=params, path="/api/captcha/get")
        res = self.sess.get(url, headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "app-key": "8fceb735082b5a529312040b58ea780b",
            "cgauthorization": self.access_token,
            "content-type": "application/x-www-form-urlencoded",
            "sign": self.sign,
            "timestamp": timestamp
        }).json()
        return res['data']['repData']

    def solve_captcha(self, mode='clickWord'):
        def h(*wargs):
            def pkcs7(m):
                return m + (chr(16 - len(m) % 16) * (16 - len(m) % 16)).encode('utf-8')

            t = wargs[1] if len(wargs) > 1 and wargs[1] != 0 else 'XwKsGlMcdPMEhR1B'
            s = t.encode('utf-8')
            i = wargs[0].encode('utf-8')
            cipher = AES.new(s, AES.MODE_ECB)
            text = base64.b64encode(cipher.encrypt(pkcs7(i))).decode('utf-8')
            return text

        def to_str(x):
            return json.dumps(x, separators=(',', ':'))

        if mode == 'clickWord':
            while True:
                data = self.solve_click_word()
                base64_img = data['originalImageBase64']
                word_list = data['wordList']
                token = data['token']
                point_arr = self.ocr_captcha(base64_img, word_list)

                if 'secretKey' in data.keys():
                    secret_key = data['secretKey']
                    point_json = h(to_str(point_arr), secret_key)
                else:
                    point_json = h(to_str(point_arr))
                params = {
                    'captchaType': mode,
                    'pointJson': point_json,
                    'token': token
                }
                timestamp = self.get_timestamp()
                self.sign = self.get_sign(timestamp=timestamp, params=params, path="/api/captcha/check")
                res = self.sess.post(self.check_captcha_url, headers={
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "zh-CN,zh;q=0.9",
                    "app-key": "8fceb735082b5a529312040b58ea780b",
                    "cgauthorization": self.access_token,
                    "content-type": "application/x-www-form-urlencoded; charset=utf-8",
                    "sign": self.sign,
                    "timestamp": timestamp
                }, params=params)
                if res.json()['data']['repCode'] == '0000':
                    return h(token + '---' + to_str(point_arr), secret_key) if 'secretKey' in data.keys() else h(
                        token + '---' + to_str(point_arr))
                else:
                    logger.info(f'验证码错误，重试中...')
                    time.sleep(0.1)
        elif mode == 'blockPuzzle':
            # 暂时没用到
            raise NotImplementedError


class LoginError(Exception):
    """Login Exception"""
    pass


def listener(event):
    jobs = schedule.get_jobs()
    if event.retval is None:
        if len(jobs) > 0:
            schedule.remove_job(schedule.get_jobs()[0].id)
    elif event.retval['code'] in [200, 409]:
        if len(jobs) > 0:
            schedule.remove_job(schedule.get_jobs()[0].id)

    if len(jobs) == 0:
        schedule.shutdown(wait=False)


def job(user, buddies, reserver, mode):
    buddy_no = ""
    for buddy in buddies:
        tmp = User(buddy['username'], buddy['password'])
        if buddy_no != "":
            buddy_no += ","
        buddy_no += str(tmp.get_buddy_no())
    logger.info(f'buddy_no: {buddy_no}')
    return user.exec(buddy_no, reserver, mode)


def main():
    config = json.load(open("./config.json", encoding="utf-8"))
    username = config['username']
    password = config['password']

    resever = Reserver()

    main_user = User(username, password)

    logger.info(args)
    schedule.add_listener(listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    run_time = datetime.datetime.strptime(args.start_time, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=tz.gettz('Asia/Shanghai'))
    if args.mode == 'interval':
        schedule.add_job(job, 'interval', seconds=10,
                         args=[main_user, config['buddies'], resever, args.mode],
                         start_date=run_time)
        schedule.print_jobs()
        schedule.start()
    elif args.mode == 'once':
        schedule.add_job(job, 'date', next_run_time=run_time, args=[main_user, config['buddies'], resever, args.mode])
        schedule.print_jobs()
        schedule.start()
    elif args.mode == 'debug':
        job(main_user, config['buddies'], resever, args.mode)


if __name__ == "__main__":
    sys.stdout = open(os.devnull, 'w')
    main()

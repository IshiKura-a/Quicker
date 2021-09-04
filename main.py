import datetime

import requests
import json
import re
import time
import hashlib
from apscheduler.schedulers.blocking import BlockingScheduler


class Reserver(object):
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.login_url = "https://zjuam.zju.edu.cn/cas/login?service=http://www.tyys.zju.edu.cn/venue-server/sso/manageLogin"
        self.info_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/day/info"
        self.order_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/order/info"
        self.submit_url = "http://www.tyys.zju.edu.cn/venue-server/api/reservation/order/submit"
        self.pay_url = "http://www.tyys.zju.edu.cn/venue-server/api/venue/finances/order/pay"
        self.sess = requests.Session()
        self.sign = ""
        self.access_token = ""
        self.deny_list = []

        self.venue_site_id = input("请输入场地编号(玉泉羽毛球场(39)):")
        self.date = input("请输入预约日期(yyyy-mm-dd):")
        candidate_str = input("请输入候选开始时间(hh:mm)，空格隔开:")
        self.candidate = [self.date + " " + i for i in candidate_str.split(" ")]
        self.n_site = input("请输入预约场数(1/2):")
        companion_str = input("请输入同伴姓名（必须一起预约过）:")
        self.companion = companion_str.split(" ")
        self.phone = input("请输入手机号:")

        print("\n-----------------")
        print("场地编号: " + str(self.venue_site_id))
        print("预约日期: " + str(self.date))
        print("候选时间: " + str(self.candidate))
        print("预约场数: " + str(self.n_site))
        print("同伴: " + str(self.companion))
        print("手机号: " + self.phone)
        print("-----------------\n")

    def login(self):
        """Login to ZJU platform"""
        res = self.sess.get(self.login_url)
        execution = re.search(
            'name="execution" value="(.*?)"', res.text).group(1)
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
            print("Login Success!")
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

    def order(self):
        while 1:
            info = self.get_info(self.venue_site_id, self.date)["data"]["reservationDateSpaceInfo"][self.date]
            space_id = None
            time_id = None
            flag = True
            for space in info:
                if flag:
                    for key, value in space.items():
                        try:
                            int(key)
                            if value["reservationStatus"] == 1 and self.candidate.count(value["startDate"]) > 0:
                                if self.deny_list.count({"spaceId": str(space["id"]), "timeId": str(key)}) == 0:
                                    if self.n_site != 1:
                                        if space[str(int(key) + 1)]["reservationStatus"] == 1:
                                            if self.deny_list.count(
                                                    {"spaceId": str(space["id"]), "timeId": str(int(key) + 1)}) == 0:
                                                space_id = str(space["id"])
                                                time_id = str(key)
                                                flag = False
                                                break
                                    else:
                                        space_id = str(space["id"])
                                        time_id = str(key)
                                        flag = False
                                        break
                        except Exception:
                            pass
                else:
                    break
            order = None
            if space_id is None:
                print("所有场次均被预约")
                return None
            elif self.n_site == 1:
                order = [{"spaceId": str(space_id), "timeId": str(time_id), "venueSpaceGroupId": None}]
            else:
                order = [{"spaceId": str(space_id), "timeId": str(time_id), "venueSpaceGroupId": None},
                         {"spaceId": str(space_id), "timeId": str(int(time_id) + 1), "venueSpaceGroupId": None}]

            timestamp = self.get_timestamp()
            order = str(order).replace(": ", ":").replace(", ", ",").replace("\'", "\"").replace("None", "null")
            params = {
                "venueSiteId": self.venue_site_id,
                "reservationDate": self.date,
                "weekStartDate": self.date,
                "reservationOrderJson": order
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
                break
            else:
                for o in order:
                    self.deny_list.append({
                        "spaceId": o["spaceId"],
                        "timeId": o["timeId"],
                    })
        buddy_list = res["data"]["buddyList"]

        buddy_ids = ""
        for buddy in sorted(buddy_list, key=lambda i: i['id']):
            if self.companion.count(buddy["name"]) != 0:
                if len(buddy_ids) != 0:
                    buddy_ids += ","
                buddy_ids += str(buddy["id"])

        params = {
            "venueSiteId": self.venue_site_id,
            "reservationDate": self.date,
            "reservationOrderJson": order,
            "phone": int(self.phone),
            "buddyIds": buddy_ids,
            "weekStartDate": self.date,
            "isOfflineTicket": 1
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

        if res["code"] == 200:
            return res
        else:
            return None

    def exec(self):
        self.login()
        time.sleep(60)
        print(self.order())

    def get_timestamp(self):
        return str(int(round(time.time() * 1000)))


class LoginError(Exception):
    """Login Exception"""
    pass


def job(reserver):
    reserver.exec()


def main():
    config = json.load(open("./config.json", encoding="utf-8"))
    username = config['username']
    password = config['password']

    schedule = BlockingScheduler()
    reserver = Reserver(username, password)

    time_str = input("请输入执行时间: ")
    run_time = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    schedule.add_job(job, 'date', next_run_time=run_time, args=[reserver])
    schedule.print_jobs()
    schedule.start()


if __name__ == "__main__":
    main()

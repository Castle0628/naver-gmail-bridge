"""끝에서 끝까지 시험: 가상의 거래처가 보낸 것처럼 꾸민 업무 메일을 네이버 메일함에 넣는다(IMAP APPEND).
그러면 클라우드 다리가 다음 실행 때 그 메일을 지메일로 넘겨야 한다.
쓰는 법: python e2e.py            → 받은편지함 1통 + 감정견적 폴더 1통 넣기
가상 주소는 example 도메인(실제로 없는 주소)이라 답장해도 진짜 사람에게 가지 않는다.
"""
import email.utils
import imaplib
import time
from email.message import EmailMessage

import bridge

FAKE_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n" + b"0" * 200_000


def fake_mail(subject: str, sender: str, body: str, fname: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = bridge.NAVER_ADDR
    msg["Cc"] = "공무팀 <gongmu@daeseong-enc.example>"
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="daeseong-enc.example")
    msg.set_content(body)
    msg.add_alternative("<html><body>" + body.replace("\n", "<br>") + "</body></html>", subtype="html")
    msg.add_attachment(FAKE_PDF, maintype="application", subtype="pdf", filename=fname)
    return msg.as_bytes()


def main():
    m = imaplib.IMAP4_SSL("imap.naver.com", 993)
    m.login(bridge.NAVER_USER, bridge.password())
    folders = {human: name for name, human in bridge.list_folders(m)}
    tag = time.strftime("%H%M")
    jobs = [
        ("INBOX", fake_mail(
            f"[대성이엔씨] 함양도서관 3층 슬래브 균열 사진 송부 (E2E {tag})",
            "박정훈 현장소장 <jh.park@daeseong-enc.example>",
            "안녕하세요, 대성이엔씨 박정훈 소장입니다.\n\n3층 슬래브 하부 균열 사진과 위치도 보내드립니다.\n"
            "검토 후 회신 부탁드립니다.\n\n박정훈 드림\n010-0000-0000",
            "3층_균열위치도.pdf")),
        ("감정견적", fake_mail(
            f"감정 견적 요청드립니다 - 누수 하자 (E2E {tag})",
            "김민서 변호사 <ms.kim@hanbit-law.example>",
            "한빛법률사무소 김민서 변호사입니다.\n\n아파트 누수 하자 감정 견적을 요청드립니다.\n"
            "소장 사본 첨부합니다.\n\n감사합니다.",
            "소장_사본.pdf")),
    ]
    for human, raw in jobs:
        box = "INBOX" if human == "INBOX" else folders[human]
        typ, d = m.append(f'"{box}"', None, imaplib.Time2Internaldate(time.time()), raw)
        print(human, typ, d)
    m.logout()


if __name__ == "__main__":
    main()

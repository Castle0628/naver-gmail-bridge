"""네이버 메일 → 지메일 다리.

네이버는 개인 메일 자동 전달이 없고, 지메일은 외부 메일 가져오기를 끄는 중이라
이 PC에서 2분마다 네이버를 IMAP으로 보고 새 메일을 지메일로 넘긴다.

- 보낸 사람 주소는 네이버 SMTP 규칙상 bkeng6331@naver.com 으로 나가지만
  이름에 원래 보낸 사람을 쓰고 Reply-To 를 원래 보낸 사람으로 둬서
  지메일에서 '답장'을 누르면 원래 보낸 사람에게 간다(보내는 주소는 네이버).
- 네이버 원본은 건드리지 않는다(읽음 표시도 안 바꿈: BODY.PEEK).
- 업무 외 폴더(프로모션·청구·결제·SNS·카페·알바몬)와 보낸/임시/휴지통/스팸은 넘기지 않는다.
- 처음 실행 때는 지금까지 메일을 '이미 본 것'으로만 기록하고 넘기지 않는다.

사용: python bridge.py            # 한 번 돌기
      python bridge.py --init     # 상태만 새로 기록(넘기지 않음)
      python bridge.py --test     # 네이버로 시험 메일 한 통 보내기
"""
import base64
import email
import email.policy
import email.utils
import html
import imaplib
import json
import logging
import os
import smtplib
import ssl
import sys
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRET = HERE / "secret.txt"          # 네이버 애플리케이션 비밀번호 한 줄 (git 제외)
STATE = HERE / "state.json"           # 폴더별 마지막으로 본 UID (git 제외)
LOG = HERE / "bridge.log"

NAVER_USER = "bkeng6331"
NAVER_ADDR = "bkeng6331@naver.com"
GMAIL_ADDR = "bkeng6331@gmail.com"
BRIDGE_HEADER = "X-Naver-Gmail-Bridge"
MAX_BYTES = 37 * 1024 * 1024          # 네이버 SMTP 한도(EHLO SIZE 39,845,888B=38MB)보다 조금 작게. 넘으면 알림만 보냄

SKIP_FOLDERS = {
    "Sent Messages", "Drafts", "Deleted Messages", "Junk", "Templates",
    "프로모션", "청구·결제", "SNS", "카페", "알바몬",
}
STRIP_HEADERS = [
    "From", "To", "Cc", "Bcc", "Reply-To", "Sender", "Return-Path", "Delivered-To",
    "Received", "DKIM-Signature", "ARC-Seal", "ARC-Message-Signature",
    "ARC-Authentication-Results", "Authentication-Results", "Received-SPF",
    "X-Original-To", "Message-ID", "X-Naver-ESV", "X-Session-IP",
]

logging.basicConfig(filename=LOG, level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")
log.addHandler(logging.StreamHandler(sys.stdout))  # 깃허브 실행 기록에도 보이게


# ---- IMAP 폴더 이름(modified UTF-7) ----
def mutf7_decode(s: str) -> str:
    out, i = "", 0
    while i < len(s):
        if s[i] == "&":
            j = s.index("-", i)
            if j == i + 1:
                out += "&"
            else:
                b = s[i + 1:j].replace(",", "/")
                b += "=" * (-len(b) % 4)
                out += base64.b64decode(b).decode("utf-16-be")
            i = j + 1
        else:
            out += s[i]
            i += 1
    return out


def dec(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def password() -> str:
    # 클라우드(깃허브)에서는 비밀 칸 NAVER_APP_PASSWORD, 이 PC에서는 secret.txt
    pw = os.environ.get("NAVER_APP_PASSWORD") or SECRET.read_text(encoding="utf-8")
    pw = pw.strip().replace(" ", "")
    if not pw:
        raise SystemExit("secret.txt 가 비어 있어요")
    return pw


def imap_login() -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL("imap.naver.com", 993, ssl_context=ssl.create_default_context())
    m.login(NAVER_USER, password())
    return m


def list_folders(m) -> list[tuple[str, str]]:
    """(서버 이름, 사람이 읽는 이름) 목록. 넘길 폴더만."""
    typ, data = m.list()
    res = []
    for raw in data:
        line = raw.decode("ascii", "replace")
        if "\\Noselect" in line:
            continue
        # 형태: (\Flags) "/" "name"  또는  (\Flags) "/" name
        name = line.rsplit(' "/" ', 1)[-1].strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        human = mutf7_decode(name)
        if name.upper() == "INBOX":
            human = "INBOX"
        top = human.split("/")[0]
        if human in SKIP_FOLDERS or top in SKIP_FOLDERS:
            continue
        res.append((name, human))
    return res


def folder_status(m, name):
    typ, data = m.status(f'"{name}"', "(UIDNEXT UIDVALIDITY)")
    txt = data[0].decode()
    import re
    nxt = int(re.search(r"UIDNEXT (\d+)", txt).group(1))
    val = int(re.search(r"UIDVALIDITY (\d+)", txt).group(1))
    return nxt, val


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def build_forward(raw: bytes, folder_human: str) -> EmailMessage:
    orig = email.message_from_bytes(raw, policy=email.policy.default)
    o_from = dec(orig.get("From"))
    o_name, o_addr = email.utils.parseaddr(o_from)
    o_reply = dec(orig.get("Reply-To")) or o_from
    o_to, o_cc = dec(orig.get("To")), dec(orig.get("Cc"))
    subject = dec(orig.get("Subject")) or "(제목 없음)"
    if folder_human != "INBOX":
        subject = f"[{folder_human}] {subject}"

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for h in STRIP_HEADERS:
        del msg[h]
    del msg["Subject"]
    shown = (o_name or o_addr or "알 수 없음").replace('"', "")
    msg["From"] = email.utils.formataddr((f"{shown} ({o_addr})", NAVER_ADDR))
    msg["To"] = GMAIL_ADDR
    msg["Reply-To"] = o_reply
    msg["Subject"] = subject
    msg["Message-ID"] = email.utils.make_msgid(domain="naver.com")
    msg["X-Original-From"] = o_from
    if o_to:
        msg["X-Original-To"] = o_to
    if o_cc:
        msg["X-Original-Cc"] = o_cc
    msg[BRIDGE_HEADER] = folder_human
    return msg


FILES_REPO = "Castle0628/naver-mail-files"   # 비공개. 큰 첨부를 Releases에 보관


def _gh(method, url, token, data=None, headers=None):
    import urllib.request
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28", **(headers or {})}
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read() or b"{}")


def upload_attachments(parts, tag: str, title: str, token: str) -> list[tuple[str, int, str]]:
    """첨부들을 비공개 저장소 릴리스에 올리고 (원래이름, 크기, 내려받기 주소) 목록을 돌려준다."""
    import urllib.parse
    rel = _gh("POST", f"https://api.github.com/repos/{FILES_REPO}/releases", token,
              {"tag_name": tag, "name": title[:120], "body": title})
    out = []
    for i, (fname, data) in enumerate(parts, 1):
        ext = Path(fname).suffix if Path(fname).suffix.isascii() else ""
        asset = f"{i:02d}{ext}" if not fname.isascii() else fname.replace(" ", "_")
        q = urllib.parse.urlencode({"name": asset, "label": fname})
        a = _gh("POST", f"https://uploads.github.com/repos/{FILES_REPO}/releases/{rel['id']}/assets?{q}",
                token, data, {"Content-Type": "application/octet-stream"})
        out.append((fname, len(data), a["browser_download_url"]))
    return out


def big_with_links(raw: bytes, folder_human: str, uid: int, token: str) -> EmailMessage:
    """37MB 넘는 메일: 첨부는 비공개 저장소에 올리고, 본문 + 내려받기 주소를 지메일로 보낸다."""
    orig = email.message_from_bytes(raw, policy=email.policy.default)
    files, keep_small = [], []
    for part in orig.walk():
        if part.is_multipart():
            continue
        fname = part.get_filename()
        if not fname and part.get_content_disposition() != "attachment":
            continue
        data = part.get_payload(decode=True) or b""
        files.append((dec(fname) or f"첨부{len(files)+1}", data))
    o_from = dec(orig.get("From"))
    o_name, o_addr = email.utils.parseaddr(o_from)
    subject = dec(orig.get("Subject")) or "(제목 없음)"
    tag = f"m{time.strftime('%Y%m%d-%H%M%S')}-{uid}"
    links = upload_attachments(files, tag, f"{subject} / {o_from}", token)

    body = orig.get_body(preferencelist=("html", "plain"))
    text = body.get_content() if body else ""
    is_html = bool(body) and body.get_content_type() == "text/html"
    rows = "".join(
        f'<li><a href="{u}">{html.escape(n)}</a> ({s/1024/1024:.1f}MB)</li>'
        for n, s, u in links)
    notice_html = (f'<div style="border:1px solid #f0b400;background:#fff8e1;padding:10px;margin-bottom:12px">'
                   f'첨부가 커서({len(raw)/1024/1024:.0f}MB) 파일은 아래 주소에서 받으세요. '
                   f'(깃허브 Castle0628 로그인 필요)<ul>{rows}</ul></div>')
    notice_txt = "첨부가 커서 파일은 아래 주소에서 받으세요(깃허브 로그인 필요):\n" + \
        "\n".join(f"- {n} ({s/1024/1024:.1f}MB): {u}" for n, s, u in links) + "\n\n"

    n = EmailMessage()
    shown = (o_name or o_addr or "알 수 없음").replace('"', "")
    n["From"] = email.utils.formataddr((f"{shown} ({o_addr})", NAVER_ADDR))
    n["To"] = GMAIL_ADDR
    n["Reply-To"] = dec(orig.get("Reply-To")) or o_from
    pre = f"[{folder_human}] " if folder_human != "INBOX" else ""
    n["Subject"] = f"{pre}[큰 첨부·링크] {subject}"
    n["X-Original-From"] = o_from
    if orig.get("To"):
        n["X-Original-To"] = dec(orig.get("To"))
    if orig.get("Cc"):
        n["X-Original-Cc"] = dec(orig.get("Cc"))
    n[BRIDGE_HEADER] = folder_human
    if is_html:
        n.set_content(notice_txt + "(HTML 메일이에요)")
        n.add_alternative(notice_html + text, subtype="html")
    else:
        n.set_content(notice_txt + text)
    return n


def too_big_notice(raw: bytes, folder_human: str) -> EmailMessage:
    orig = email.message_from_bytes(raw, policy=email.policy.default)
    o_from = dec(orig.get("From"))
    o_name, o_addr = email.utils.parseaddr(o_from)
    n = EmailMessage()
    n["From"] = email.utils.formataddr((f"{o_name or o_addr} ({o_addr})", NAVER_ADDR))
    n["To"] = GMAIL_ADDR
    n["Reply-To"] = dec(orig.get("Reply-To")) or o_from
    n["Subject"] = f"[첨부 큼·네이버에서 확인] {dec(orig.get('Subject'))}"
    n[BRIDGE_HEADER] = folder_human
    n.set_content(
        f"첨부가 커서({len(raw)//1024//1024}MB) 지메일로 옮기지 못했어요.\n"
        f"네이버 메일 '{folder_human}' 폴더에서 확인해 주세요.\n\n보낸 사람: {o_from}\n")
    return n


def send(msgs: list[EmailMessage]):
    if not msgs:
        return
    with smtplib.SMTP_SSL("smtp.naver.com", 465, context=ssl.create_default_context()) as s:
        s.login(NAVER_USER, password())
        for msg in msgs:
            s.send_message(msg, from_addr=NAVER_ADDR, to_addrs=[GMAIL_ADDR])


DELETE_AFTER_SEC = int(os.environ.get("BRIDGE_DELETE_AFTER_SEC", 3 * 24 * 3600))  # 3일
TRASH = "Deleted Messages"


def purge(m, st, now=None) -> int:
    """지메일로 넘긴 지 3일 지난 원본을 네이버에서 영구 삭제(휴지통까지).
    st["_pending"] = {폴더: [[uid, 넘긴시각, Message-ID], ...]}. 사용자 승인: 2026-11-20 '3일 뒤 삭제'."""
    now = now or time.time()
    pend = st.setdefault("_pending", {})
    gone = 0
    for name, items in list(pend.items()):
        due = [it for it in items if now - it[1] >= DELETE_AFTER_SEC]
        if not due:
            continue
        typ, _ = m.select(f'"{name}"')
        if typ != "OK":
            continue
        uids = ",".join(str(it[0]) for it in due)
        m.uid("store", uids, "+FLAGS.SILENT", r"(\Deleted)")
        m.uid("expunge", uids)
        gone += len(due)
        # 네이버가 휴지통으로 옮겼다면 거기서도 지운다(용량에 남지 않게)
        mids = [it[2] for it in due if it[2]]
        if mids and m.select(f'"{TRASH}"')[0] == "OK":
            for mid in mids:
                typ, d = m.uid("search", None, "HEADER", "Message-ID", mid)
                tu = d[0].split() if d and d[0] else []
                if tu:
                    s = b",".join(tu).decode()
                    m.uid("store", s, "+FLAGS.SILENT", r"(\Deleted)")
                    m.uid("expunge", s)
        pend[name] = [it for it in items if it not in due]
        log.info("네이버 원본 삭제 %s %d통", st.get(name, {}).get("label", name), len(due))
        save_state(st)
    return gone


def quota(m) -> str:
    try:
        typ, d = m.getquotaroot("INBOX")
        txt = b" ".join(x for x in d[-1] if isinstance(x, bytes)) if isinstance(d[-1], list) else d[-1]
        import re
        u, lim = map(int, re.search(rb"STORAGE (\d+) (\d+)", txt if isinstance(txt, bytes) else str(txt).encode()).groups())
        return f"{u/1024/1024:.2f}GB / {lim/1024/1024:.0f}GB"
    except Exception as e:
        return f"알 수 없음({e})"


def run(init_only=False):
    st = load_state()
    m = imap_login()
    sent = 0
    try:
        for name, human in list_folders(m):
            nxt, val = folder_status(m, name)
            cur = st.get(name)
            if init_only or cur is None or cur.get("validity") != val:
                st[name] = {"validity": val, "next": nxt, "label": human}
                continue
            if nxt <= cur["next"]:
                continue
            m.select(f'"{name}"', readonly=True)
            typ, data = m.uid("search", None, f"UID {cur['next']}:*")
            uids = [int(u) for u in data[0].split() if int(u) >= cur["next"]]
            for uid in sorted(uids):
                typ, fd = m.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in fd if isinstance(p, tuple)), None)
                if raw is None:
                    continue
                if BRIDGE_HEADER.encode() in raw[:20000]:
                    pass  # 우리가 만든 메일이면 넘기지 않음
                else:
                    token = os.environ.get("FILES_TOKEN")
                    if len(raw) <= MAX_BYTES:
                        out, done = build_forward(raw, human), True
                    elif token:
                        try:
                            out, done = big_with_links(raw, human, uid, token), True
                        except Exception as e:
                            log.exception("큰 첨부 올리기 실패 uid=%s: %s", uid, e)
                            out, done = too_big_notice(raw, human), False
                    else:
                        out, done = too_big_notice(raw, human), False
                    send([out])
                    sent += 1
                    log.info("넘김 %s uid=%s %s", human, uid, out["Subject"])
                    if done:  # 지메일(또는 비공개 저장소)에 다 옮긴 것만 3일 뒤 삭제 대상
                        mid = email.message_from_bytes(raw[:200000]).get("Message-ID", "")
                        st.setdefault("_pending", {}).setdefault(name, []).append(
                            [uid, time.time(), str(mid).strip()])
                cur["next"] = uid + 1
                save_state(st)  # 한 통마다 저장 → 중간에 끊겨도 두 번 안 보냄
            cur["next"] = max(cur["next"], nxt)
        save_state(st)
        if not init_only:
            purge(m, st)
            log.info("네이버 용량 %s", quota(m))
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return sent


def send_test():
    t = EmailMessage()
    t["From"] = NAVER_ADDR
    t["To"] = NAVER_ADDR
    t["Subject"] = "[다리 시험] 네이버→지메일 넘기기 시험"
    t.set_content("이 메일이 지메일 받은편지함에 보이면 다리가 동작하는 거예요.")
    with smtplib.SMTP_SSL("smtp.naver.com", 465, context=ssl.create_default_context()) as s:
        s.login(NAVER_USER, password())
        s.send_message(t)


if __name__ == "__main__":
    try:
        if "--test" in sys.argv:
            send_test()
            print("시험 메일 보냄")
        else:
            n = run(init_only="--init" in sys.argv)
            print(f"넘긴 메일 {n}통")
    except Exception as e:
        log.exception("실패: %s", e)
        print("실패:", e)
        sys.exit(1)

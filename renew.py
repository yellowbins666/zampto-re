#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import subprocess
import time
from urllib.parse import urlparse

import requests
from seleniumbase import SB


# ============================================================
# Configuration
# ============================================================
EMAIL = os.environ.get("ZAM_PTO_EMAIL", "").strip()
# Keep password exactly as stored in GitHub Secret. Do not strip spaces.
PASSWORD = os.environ.get("ZAM_PTO_PASSWORD", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

IS_PROXY = os.environ.get("IS_PROXY", "true").strip().lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "http://127.0.0.1:1081").strip()

BASE_URL = "https://dash.zampto.net"
LOGIN_URL = f"{BASE_URL}/auth/login"
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"

LOGIN_WAIT_SECONDS = 30
TURNSTILE_ATTEMPTS = 3
TURNSTILE_TIMEOUT_PER_ATTEMPT = 20


# ============================================================
# Helpers
# ============================================================
def now_cn() -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    return time.strftime("%Y-%m-%d %H:%M:%S", local_time)


def mask_email(value: str) -> str:
    if "@" not in value:
        return (value[:2] + "****") if value else "未配置"
    name, domain = value.split("@", 1)
    if len(name) <= 4:
        masked = name[:1] + "***"
    else:
        masked = f"{name[:2]}****{name[-2:]}"
    return f"{masked}@{domain}"


def safe_current_url(sb) -> str:
    try:
        return sb.get_current_url() or ""
    except Exception:
        return ""


def safe_title(sb) -> str:
    try:
        return sb.get_title() or ""
    except Exception:
        return ""


def safe_body_text(sb) -> str:
    try:
        return (sb.get_text("body") or "").strip()
    except Exception:
        return ""


# ============================================================
# Telegram: text + screenshot
# ============================================================
def send_tg_message(
    status_icon: str,
    status_text: str,
    detail: str = "",
    photo_path: str | None = None,
) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return False

    text = (
        "🇫🇷 ZamPTO 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 续期账户: {mask_email(EMAIL)}\n"
        f"⏱️ 操作时间: {now_cn()}"
    )
    if detail:
        text += f"\n📝 详情: {detail[:850]}"

    # First choice: sendPhoto. If it fails, fall back to sendMessage so the
    # failure reason is never lost.
    if photo_path:
        photo_path = os.path.abspath(photo_path)
        if os.path.isfile(photo_path):
            try:
                size = os.path.getsize(photo_path)
                print(f"📷 准备发送截图: {photo_path} ({size} bytes)")
                url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
                with open(photo_path, "rb") as f:
                    resp = requests.post(
                        url,
                        data={"chat_id": TG_CHAT_ID, "caption": text},
                        files={"photo": (os.path.basename(photo_path), f, "image/png")},
                        timeout=30,
                    )
                if resp.ok:
                    print("📩 Telegram 截图发送成功！")
                    return True
                print(
                    f"⚠️ Telegram sendPhoto 失败: HTTP {resp.status_code} "
                    f"{resp.text[:300]}"
                )
            except Exception as exc:
                print(f"⚠️ Telegram sendPhoto 异常: {exc}")
        else:
            print(f"⚠️ 截图文件不存在，改发纯文本: {photo_path}")

    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=15,
        )
        if resp.ok:
            print("📩 Telegram 文本通知发送成功！")
            return True
        print(
            f"⚠️ Telegram sendMessage 失败: HTTP {resp.status_code} "
            f"{resp.text[:300]}"
        )
    except Exception as exc:
        print(f"⚠️ Telegram sendMessage 异常: {exc}")
    return False


def save_screenshot(sb, filename: str) -> str | None:
    path = os.path.abspath(filename)
    try:
        sb.save_screenshot(path)
        if os.path.isfile(path):
            print(f"📸 已保存截图: {path} ({os.path.getsize(path)} bytes)")
            return path
        print(f"⚠️ SeleniumBase 未生成截图文件: {path}")
    except Exception as exc:
        print(f"⚠️ 保存截图失败: {exc}")
    return None


def notify_login_failure(sb, reason: str, filename: str) -> None:
    current_url = safe_current_url(sb)
    title = safe_title(sb)
    cf_state = "检测到" if turnstile_exists(sb) else "未检测到"
    detail = (
        f"{reason}; URL={current_url or 'unknown'}; "
        f"Title={title or 'unknown'}; Turnstile={cf_state}"
    )
    shot = save_screenshot(sb, filename)
    send_tg_message("❌", "登录失败", detail, shot)


# ============================================================
# Turnstile handling
# ============================================================
TURNSTILE_EXISTS_JS = r"""
(function () {
    return !!document.querySelector('input[name="cf-turnstile-response"]') ||
           !!document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
           !!document.querySelector('.cf-turnstile');
})();
"""

TURNSTILE_SOLVED_JS = r"""
(function () {
    var input = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(input && input.value && input.value.length > 20);
})();
"""

TURNSTILE_REVEAL_JS = r"""
(function () {
    var input = document.querySelector('input[name="cf-turnstile-response"]');
    if (input) {
        var el = input;
        for (var i = 0; i < 20; i++) {
            el = el.parentElement;
            if (!el) break;
            var style = window.getComputedStyle(el);
            if (style.overflow === 'hidden' ||
                style.overflowX === 'hidden' ||
                style.overflowY === 'hidden') {
                el.style.overflow = 'visible';
            }
            el.style.minWidth = 'max-content';
        }
    }

    document.querySelectorAll('iframe').forEach(function (frame) {
        if (frame.src && frame.src.includes('challenges.cloudflare.com')) {
            frame.style.width = '300px';
            frame.style.height = '70px';
            frame.style.minWidth = '300px';
            frame.style.display = 'block';
            frame.style.visibility = 'visible';
            frame.style.opacity = '1';
        }
    });

    var widget = document.querySelector('.cf-turnstile');
    if (widget) widget.scrollIntoView({block: 'center', inline: 'center'});
    return true;
})();
"""


def turnstile_exists(sb) -> bool:
    try:
        return bool(sb.execute_script(TURNSTILE_EXISTS_JS))
    except Exception:
        return False


def turnstile_solved(sb) -> bool:
    try:
        return bool(sb.execute_script(TURNSTILE_SOLVED_JS))
    except Exception:
        return False


def activate_browser_window() -> None:
    # Under xvfb-run, SeleniumBase's UC GUI click needs the Chrome window to
    # be the active X11 window. xdotool is already installed by the workflow.
    for cls in ("chrome", "chromium", "Chromium", "Chrome", "google-chrome"):
        try:
            proc = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", cls],
                capture_output=True,
                text=True,
                timeout=3,
            )
            windows = [w.strip() for w in proc.stdout.splitlines() if w.strip()]
            if windows:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", windows[0]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                time.sleep(0.3)
                return
        except Exception:
            continue


def handle_turnstile(
    sb,
    max_attempts: int = TURNSTILE_ATTEMPTS,
    timeout_per_attempt: int = TURNSTILE_TIMEOUT_PER_ATTEMPT,
) -> bool:
    print(
        f"🔍 处理 Cloudflare Turnstile（最多 {max_attempts} 次，"
        f"每次 {timeout_per_attempt} 秒）..."
    )

    if turnstile_solved(sb):
        print("✅ Turnstile 已经有有效 token")
        return True

    for _ in range(3):
        try:
            sb.execute_script(TURNSTILE_REVEAL_JS)
        except Exception:
            pass
        time.sleep(0.5)

    for attempt in range(1, max_attempts + 1):
        if turnstile_solved(sb):
            print(f"✅ Turnstile 在第 {attempt} 次尝试前已完成")
            return True

        print(f"🖱️ 第 {attempt}/{max_attempts} 次尝试处理 Turnstile...")
        activate_browser_window()

        try:
            sb.execute_script(TURNSTILE_REVEAL_JS)
        except Exception:
            pass

        started = time.time()
        try:
            sb.uc_gui_click_captcha()
        except Exception as exc:
            print(f"⚠️ uc_gui_click_captcha 调用异常: {exc}")

        while time.time() - started < timeout_per_attempt:
            if turnstile_solved(sb):
                elapsed = time.time() - started
                print(f"✅ Turnstile 通过，耗时 {elapsed:.1f} 秒")
                return True
            time.sleep(0.5)

        print(f"⚠️ 第 {attempt} 次未通过")

    print("❌ Turnstile 多次尝试后仍未通过")
    return False


# ============================================================
# Login
# ============================================================
def read_login_error(sb) -> str:
    body = safe_body_text(sb)
    if not body:
        return ""

    lowered = body.lower()
    keywords = (
        "invalid credentials",
        "invalid credential",
        "wrong password",
        "incorrect password",
        "invalid email",
        "authentication failed",
    )
    if any(key in lowered for key in keywords):
        # Return a short, useful excerpt instead of the whole page.
        compact = " ".join(body.split())
        return compact[:500]
    return ""


def login(sb) -> bool:
    print("\n" + "#" * 32)
    print("   开始 ZamPTO 登录")
    print("#" * 32)
    print(f"🌐 打开登录页面: {LOGIN_URL}")

    try:
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=8)
    except Exception as exc:
        print(f"❌ 打开登录页面失败: {exc}")
        notify_login_failure(sb, f"打开登录页面失败: {exc}", "login_open_fail.png")
        return False

    print("⏳ 等待登录表单加载...")
    try:
        sb.wait_for_element_visible(EMAIL_SELECTOR, timeout=30)
        sb.wait_for_element_visible(PASSWORD_SELECTOR, timeout=30)
        print("✅ 登录表单加载成功")
    except Exception as exc:
        print(f"❌ 登录表单加载失败: {exc}")
        notify_login_failure(sb, f"登录表单加载失败: {exc}", "login_form_fail.png")
        return False

    # Cookie consent can cover the form/widget on some runs.
    try:
        for button in sb.find_elements("button"):
            text = (button.text or "").strip().lower()
            if text in {"accept", "accept all", "agree", "同意", "接受", "全部接受"}:
                button.click()
                print("🍪 已处理 Cookie 同意按钮")
                time.sleep(1)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱 ({EMAIL_SELECTOR})...")
    try:
        sb.update_text(EMAIL_SELECTOR, EMAIL)
        print(f"🔑 填写密码 ({PASSWORD_SELECTOR})...")
        sb.update_text(PASSWORD_SELECTOR, PASSWORD)
    except Exception as exc:
        print(f"❌ 输入账号密码失败: {exc}")
        notify_login_failure(sb, f"输入账号密码失败: {exc}", "login_input_fail.png")
        return False

    time.sleep(1)

    # Important ordering: process the challenge before submitting the form.
    if turnstile_exists(sb):
        print("🛡️ 登录页检测到 Turnstile")
        if not handle_turnstile(sb):
            print("❌ 登录页 Turnstile 未完成")
            notify_login_failure(
                sb,
                "登录页 Turnstile 未完成，未提交登录表单",
                "login_turnstile_fail.png",
            )
            return False
    else:
        print("ℹ️ 登录页当前未检测到 Turnstile")

    print("🖱️ 提交登录表单...")
    try:
        sb.press_keys(PASSWORD_SELECTOR, "\n")
    except Exception as exc:
        print(f"⚠️ 回车提交失败，尝试点击 Login: {exc}")
        try:
            sb.execute_script(r"""
                (function () {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var text = (buttons[i].textContent || '').trim().toLowerCase();
                        if (text === 'login' || text === 'sign in') {
                            buttons[i].click();
                            return true;
                        }
                    }
                    return false;
                })();
            """)
        except Exception as inner_exc:
            notify_login_failure(
                sb,
                f"提交登录表单失败: {inner_exc}",
                "login_submit_fail.png",
            )
            return False

    print("⏳ 等待登录结果...")
    login_paths = {"/auth/login", "/login"}

    for second in range(1, LOGIN_WAIT_SECONDS + 1):
        time.sleep(1)
        current_url = safe_current_url(sb)
        path = urlparse(current_url).path.rstrip("/").lower() if current_url else ""

        error_text = read_login_error(sb)
        if error_text:
            print("❌ 页面返回账号/密码错误提示")
            notify_login_failure(
                sb,
                f"账号或密码被页面拒绝: {error_text}",
                "login_credentials_fail.png",
            )
            return False

        # If the challenge appears again after submit, log it clearly.
        if turnstile_exists(sb) and not turnstile_solved(sb):
            print(f"⚠️ 提交后再次检测到未完成的 Turnstile（第 {second} 秒）")

        if path and path not in login_paths:
            print("✅ 登录成功，已离开登录页")
            print(f"📄 当前 URL: {current_url}")
            return True

        try:
            email_present = sb.is_element_present(EMAIL_SELECTOR)
            password_present = sb.is_element_present(PASSWORD_SELECTOR)
            if not email_present and not password_present:
                print("✅ 登录表单已消失，判定登录成功")
                return True
        except Exception:
            pass

    print(f"❌ 登录超时（{LOGIN_WAIT_SECONDS} 秒）")
    notify_login_failure(
        sb,
        f"登录 {LOGIN_WAIT_SECONDS} 秒后仍停留在登录页",
        "login_timeout.png",
    )
    return False


# ============================================================
# Server renewal helpers
# ============================================================
def parse_duration_minutes(text: str) -> int | None:
    if not text:
        return None
    value = text.strip()
    if value.lower() == "expired":
        return 0

    d = re.search(r"(\d+)d", value, re.I)
    h = re.search(r"(\d+)h", value, re.I)
    m = re.search(r"(\d+)m", value, re.I)
    if not any((d, h, m)):
        return None

    days = int(d.group(1)) if d else 0
    hours = int(h.group(1)) if h else 0
    minutes = int(m.group(1)) if m else 0
    return days * 1440 + hours * 60 + minutes


def format_minutes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value <= 0:
        return "Expired"
    return f"{value // 1440}d {(value % 1440) // 60}h {value % 60}m"


def extract_remaining_minutes(sb) -> int | None:
    try:
        page = sb.get_page_source()
    except Exception:
        page = ""

    if page:
        if re.search(
            r"Expiry\s*\(Next Renewal\).*?Expired",
            page,
            re.I | re.S,
        ):
            return 0

        match = re.search(
            r"Expiry\s*\(Next Renewal\).*?<span[^>]*>\s*"
            r"((?:\d+d\s*)?(?:\d+h\s*)?(?:\d+m\s*)?)\s*</span>",
            page,
            re.I | re.S,
        )
        if match:
            parsed = parse_duration_minutes(match.group(1))
            if parsed is not None:
                return parsed

    # Raw string avoids Python's "invalid escape sequence \\d" warning.
    js_extract = r"""
    (function () {
        var spans = document.querySelectorAll(
            'span.font-medium.text-foreground, span.text-foreground, span.text-red-400'
        );
        for (var i = 0; i < spans.length; i++) {
            var text = (spans[i].textContent || '').trim();
            if (text.toLowerCase() === 'expired') return 'Expired';
            if (/\d+[dhm]/.test(text)) return text;
        }
        return null;
    })();
    """
    try:
        return parse_duration_minutes(sb.execute_script(js_extract))
    except Exception:
        return None


def get_server_ids(sb) -> list[str]:
    print("🔍 正在提取服务器 ID 列表...")
    time.sleep(3)
    ids: list[str] = []

    try:
        page = sb.get_page_source()
        ids.extend(re.findall(r"ID:\s*(\d+)", page, re.I))
        ids.extend(re.findall(r"/server\?id=(\d+)", page, re.I))
    except Exception as exc:
        print(f"⚠️ 页面源码提取 ID 失败: {exc}")

    current = safe_current_url(sb)
    match = re.search(r"[?&]id=(\d+)", current)
    if match:
        ids.append(match.group(1))

    # Preserve order while removing duplicates.
    unique = list(dict.fromkeys(ids))
    if unique:
        print(f"✅ 找到 {len(unique)} 个服务器 ID: {unique}")
    else:
        print("❌ 未能提取到服务器 ID")
    return unique


def click_renew_button(sb) -> bool:
    script = r"""
    (function () {
        var buttons = document.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
            var text = (buttons[i].textContent || '').trim();
            if (text === 'Renew Server') {
                buttons[i].scrollIntoView({behavior: 'instant', block: 'center'});
                buttons[i].click();
                return true;
            }
        }
        return false;
    })();
    """
    try:
        return bool(sb.execute_script(script))
    except Exception as exc:
        print(f"⚠️ 点击 Renew Server 异常: {exc}")
        return False


def renew_one_server(sb, server_id: str, index: int) -> dict:
    result = {
        "index": index,
        "server_id": server_id,
        "status": "unknown",
        "detail": "",
    }

    detail_url = f"{BASE_URL}/server?id={server_id}"
    print(f"\n🔄 处理服务器 #{index + 1}: ID={server_id}")
    print(f"🌐 {detail_url}")

    try:
        sb.get(detail_url)
        time.sleep(3)
    except Exception as exc:
        result["status"] = "error"
        result["detail"] = f"打开详情页失败: {exc}"
        shot = save_screenshot(sb, f"server_{server_id}_open_fail.png")
        send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], shot)
        return result

    current = safe_current_url(sb)
    if "/server" not in urlparse(current).path.lower():
        result["status"] = "failed"
        result["detail"] = f"未进入详情页，当前 URL={current}"
        shot = save_screenshot(sb, f"server_{server_id}_not_detail.png")
        send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], shot)
        return result

    old_minutes = extract_remaining_minutes(sb)
    print(f"📅 原始剩余时间: {format_minutes(old_minutes)}")

    print("🖱️ 点击 Renew Server...")
    if not click_renew_button(sb):
        result["status"] = "error"
        result["detail"] = "找不到或无法点击 Renew Server 按钮"
        shot = save_screenshot(sb, f"server_{server_id}_renew_click_fail.png")
        send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], shot)
        return result

    time.sleep(5)
    quick_minutes = extract_remaining_minutes(sb)
    if old_minutes is not None and quick_minutes is not None:
        change = quick_minutes - old_minutes
        if change > 1000 or (old_minutes == 0 and quick_minutes > 0):
            result["status"] = "success"
            result["detail"] = (
                f"续期成功，时间从 {format_minutes(old_minutes)} "
                f"变为 {format_minutes(quick_minutes)}"
            )
            shot = save_screenshot(sb, f"renew_success_{server_id}.png")
            send_tg_message("✅", f"服务器 {server_id} 续期成功", result["detail"], shot)
            return result

    if turnstile_exists(sb) and not turnstile_solved(sb):
        print("🛡️ 续期阶段检测到 Turnstile")
        if not handle_turnstile(sb):
            print("⚠️ 续期 Turnstile 未通过，仍继续刷新检查实际结果")

    print("⏳ 等待后重新加载详情页检查最终状态...")
    time.sleep(5)
    try:
        sb.get(detail_url)
        time.sleep(3)
    except Exception as exc:
        print(f"⚠️ 重新加载详情页失败: {exc}")

    new_minutes = extract_remaining_minutes(sb)
    print(f"📅 最终剩余时间: {format_minutes(new_minutes)}")

    if old_minutes is not None and new_minutes is not None:
        change = new_minutes - old_minutes
        if change > 1000 or (old_minutes == 0 and new_minutes > 0):
            result["status"] = "success"
            result["detail"] = (
                f"续期成功，时间从 {format_minutes(old_minutes)} "
                f"变为 {format_minutes(new_minutes)}，增加 {change} 分钟"
            )
        elif -10 < change < 100:
            result["status"] = "skipped"
            result["detail"] = f"剩余时间变化不明显: {change} 分钟"
        else:
            result["status"] = "unknown"
            result["detail"] = f"时间变化异常: {change} 分钟"
    else:
        result["status"] = "unknown"
        result["detail"] = (
            f"无法确认结果，原始={format_minutes(old_minutes)}，"
            f"最终={format_minutes(new_minutes)}"
        )

    shot = save_screenshot(sb, f"renew_result_{server_id}.png")
    if result["status"] == "success":
        send_tg_message("✅", f"服务器 {server_id} 续期成功", result["detail"], shot)
    else:
        send_tg_message("⚠️", f"服务器 {server_id} 续期状态异常", result["detail"], shot)
    return result


def renew_all_servers(sb) -> list[dict]:
    print("\n" + "#" * 32)
    print("   开始 ZamPTO 自动续期")
    print("#" * 32)

    server_ids = get_server_ids(sb)
    if not server_ids:
        shot = save_screenshot(sb, "no_servers.png")
        send_tg_message("❌", "执行失败", "未提取到任何服务器 ID", shot)
        return []

    results = []
    for index, server_id in enumerate(server_ids):
        result = renew_one_server(sb, server_id, index)
        results.append(result)
        print(
            f"📊 #{index + 1} ID={server_id}: "
            f"{result['status']} - {result['detail']}"
        )

    total = len(results)
    success = sum(r["status"] == "success" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    failed = total - success - skipped

    summary = (
        f"续期完成：共 {total} 个服务器\n"
        f"✅ 成功: {success}\n"
        f"⏭️ 跳过: {skipped}\n"
        f"❌ 失败/未知: {failed}"
    )
    detail = "\n".join(
        f"#{r['index'] + 1} ID={r['server_id']}: {r['status']} - {r['detail']}"
        for r in results
    )

    print("\n" + "=" * 50)
    print(summary)
    print(detail)
    print("=" * 50)
    send_tg_message("📋", summary, detail)
    return results


# ============================================================
# Main
# ============================================================
def main() -> None:
    print("#" * 40)
    print("   ZamPTO 自动登录续期 - 修复版")
    print("#" * 40)

    if not EMAIL or PASSWORD == "":
        print("❌ 未配置 ZAM_PTO_EMAIL 或 ZAM_PTO_PASSWORD")
        send_tg_message("❌", "账号环境变量未配置")
        raise SystemExit(1)

    sb_kwargs = {
        "uc": True,
        "headless": False,
    }

    if IS_PROXY:
        print(f"🔗 使用本地代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🌐 未启用代理，使用直连")

    with SB(**sb_kwargs) as sb:
        try:
            try:
                sb.open("https://api.ip.sb/ip")
                exit_ip = (sb.get_text("body") or "").strip()
                print(f"📍 当前出口 IP: {exit_ip}")
            except Exception as exc:
                print(f"⚠️ 无法获取出口 IP: {exc}")
                if IS_PROXY:
                    shot = save_screenshot(sb, "proxy_check_fail.png")
                    send_tg_message("❌", "代理连接失败", str(exc), shot)
                    raise SystemExit(1)

            if not login(sb):
                # login() already sent a screenshot notification. Do not send
                # another text-only "登录失败" message here.
                print("\n❌ 登录失败，终止续期操作。")
                raise SystemExit(1)

            print("\n🎉 登录流程成功")
            renew_all_servers(sb)

        except SystemExit:
            raise
        except Exception as exc:
            print(f"❌ 程序运行异常: {exc}")
            shot = save_screenshot(sb, "runtime_exception.png")
            send_tg_message("❌", "程序运行异常", str(exc), shot)
            raise SystemExit(1)


if __name__ == "__main__":
    main()

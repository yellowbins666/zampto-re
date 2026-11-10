#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests
from seleniumbase import SB

# ============================================================
# 基础配置
# ============================================================

SCRIPT_VERSION = "2026-08-23-wait25-tgshot-v1"

EMAIL = os.environ.get("ZAM_PTO_EMAIL", "").strip()
PASSWORD = os.environ.get("ZAM_PTO_PASSWORD", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

IS_PROXY = os.environ.get("IS_PROXY", "true").strip().lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "http://127.0.0.1:1081").strip()

BASE_URL = "https://dash.zampto.net"
LOGIN_URL = f"{BASE_URL}/auth/login"
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"

# 强制限定在 20~30 秒，默认 25 秒。
try:
    LOGIN_STABILIZE_SECONDS = int(os.environ.get("LOGIN_STABILIZE_SECONDS", "25"))
except ValueError:
    LOGIN_STABILIZE_SECONDS = 25
LOGIN_STABILIZE_SECONDS = max(20, min(30, LOGIN_STABILIZE_SECONDS))

# ============================================================
# 通用工具
# ============================================================


def now_cn_str() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def mask_email(value: str) -> str:
    if "@" not in value:
        if not value:
            return "未配置"
        return value[:2] + "****"

    name, domain = value.split("@", 1)
    if len(name) <= 4:
        return f"{name}@{domain}"
    return f"{name[:2]}****{name[-2:]}@{domain}"


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


# ============================================================
# Telegram 通知：支持图片，图片失败自动退回文字消息
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
        f"⏱️ 操作时间: {now_cn_str()}"
    )
    if detail:
        text += f"\n📝 详情: {detail[:700]}"

    # 优先发送截图。
    if photo_path:
        if os.path.isfile(photo_path):
            try:
                file_size = os.path.getsize(photo_path)
                print(f"📸 准备发送截图到 Telegram: {photo_path} ({file_size} bytes)")
                url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
                with open(photo_path, "rb") as fh:
                    response = requests.post(
                        url,
                        data={"chat_id": TG_CHAT_ID, "caption": text[:1000]},
                        files={
                            "photo": (
                                os.path.basename(photo_path),
                                fh,
                                "image/png",
                            )
                        },
                        timeout=30,
                    )

                if response.ok:
                    print("📩 Telegram 截图发送成功！")
                    return True

                print(
                    "⚠️ Telegram sendPhoto 失败: "
                    f"HTTP {response.status_code} - {response.text[:500]}"
                )
            except Exception as exc:
                print(f"⚠️ Telegram 截图发送异常: {exc}")
        else:
            print(f"⚠️ 截图文件不存在，改为发送纯文字: {photo_path}")

    # 图片失败或没有图片时，退回文字。
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        response = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=15,
        )
        if response.ok:
            print("📩 Telegram 文字通知发送成功！")
            return True

        print(
            "⚠️ Telegram sendMessage 失败: "
            f"HTTP {response.status_code} - {response.text[:500]}"
        )
    except Exception as exc:
        print(f"⚠️ Telegram 文字发送异常: {exc}")

    return False


def save_screenshot(sb, path: str) -> bool:
    try:
        result = sb.save_screenshot(path)
        exists = os.path.isfile(path)
        if exists:
            print(f"📸 截图已保存: {path} ({os.path.getsize(path)} bytes)")
        else:
            print(f"⚠️ save_screenshot 返回 {result!r}，但文件不存在: {path}")
        return exists
    except Exception as exc:
        print(f"⚠️ 保存截图失败 {path}: {exc}")
        return False


def notify_login_failure(sb, reason: str, screenshot_name: str) -> None:
    url = safe_current_url(sb)
    title = safe_title(sb)
    detail = reason
    if url:
        detail += f" | URL={url}"
    if title:
        detail += f" | Title={title}"

    if save_screenshot(sb, screenshot_name):
        send_tg_message("❌", "登录失败", detail, screenshot_name)
    else:
        send_tg_message("❌", "登录失败", detail)


# ============================================================
# Cloudflare Turnstile
# ============================================================

# 这里全部使用 raw string，避免 Python 对 JavaScript 中的 \d 产生
# SyntaxWarning: invalid escape sequence。
_TURNSTILE_EXISTS_JS = r"""
(function () {
    return !!document.querySelector('input[name="cf-turnstile-response"]') ||
           !!document.querySelector('iframe[src*="challenges.cloudflare"]') ||
           !!document.querySelector('.cf-turnstile');
})();
"""

_TURNSTILE_SOLVED_JS = r"""
(function () {
    var input = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(input && input.value && input.value.length > 20);
})();
"""

_TURNSTILE_EXPAND_JS = r"""
(function () {
    var input = document.querySelector('input[name="cf-turnstile-response"]');
    if (input) {
        var el = input;
        for (var i = 0; i < 20; i++) {
            el = el.parentElement;
            if (!el) break;
            var style = window.getComputedStyle(el);
            if (
                style.overflow === 'hidden' ||
                style.overflowX === 'hidden' ||
                style.overflowY === 'hidden'
            ) {
                el.style.overflow = 'visible';
            }
        }
    }

    document.querySelectorAll('iframe').forEach(function (frame) {
        if (frame.src && frame.src.includes('challenges.cloudflare')) {
            frame.style.width = '320px';
            frame.style.height = '75px';
            frame.style.minWidth = '320px';
            frame.style.display = 'block';
            frame.style.visibility = 'visible';
            frame.style.opacity = '1';
        }
    });

    return true;
})();
"""


def turnstile_exists(sb) -> bool:
    try:
        return bool(sb.execute_script(_TURNSTILE_EXISTS_JS))
    except Exception:
        return False


def turnstile_solved(sb) -> bool:
    try:
        return bool(sb.execute_script(_TURNSTILE_SOLVED_JS))
    except Exception:
        return False


def turnstile_challenge_visible(sb) -> bool:
    """页面上是否存在【可见的】Turnstile 挑战组件。

    turnstile_exists 会命中隐藏的 cf-turnstile-response 输入框——服务器
    详情页模板里就藏着一个永远不会被解的（2026-11-10 实测：续期阶段
    因此对着空气空转了 3×20 秒）。续期阶段只认可见的挑战 iframe。
    """
    try:
        return bool(
            sb.execute_script(
                """
(function () {
    var frames = document.querySelectorAll('iframe[src*="challenges.cloudflare"]');
    for (var i = 0; i < frames.length; i++) {
        var r = frames[i].getBoundingClientRect();
        if (r.width > 30 && r.height > 20) return true;
    }
    return false;
})();
                """
            )
        )
    except Exception:
        return False


def handle_turnstile(sb, max_attempts: int = 3, timeout_per_attempt: int = 20) -> bool:
    print(
        "🔍 处理 Cloudflare Turnstile 验证"
        f"（最多 {max_attempts} 次，每次 {timeout_per_attempt} 秒超时）..."
    )

    # 先给挑战组件一点时间完成渲染。
    time.sleep(2)

    if turnstile_solved(sb):
        print("✅ Turnstile 已静默通过")
        return True

    for _ in range(3):
        try:
            sb.execute_script(_TURNSTILE_EXPAND_JS)
        except Exception:
            pass
        time.sleep(0.5)

    for attempt in range(1, max_attempts + 1):
        if turnstile_solved(sb):
            print(f"✅ Turnstile 在第 {attempt} 次尝试前已经完成")
            return True

        print(f"🖱️ 第 {attempt}/{max_attempts} 次调用 uc_gui_click_captcha...")
        started = time.time()

        try:
            sb.uc_gui_click_captcha()
        except Exception as exc:
            print(f"⚠️ uc_gui_click_captcha 调用异常: {exc}")

        while time.time() - started < timeout_per_attempt:
            time.sleep(0.5)
            if turnstile_solved(sb):
                elapsed = time.time() - started
                print(f"✅ Turnstile 通过（第 {attempt} 次，耗时 {elapsed:.1f} 秒）")
                return True

        print(f"⏱️ 第 {attempt} 次尝试超时（{timeout_per_attempt} 秒）")

        # 下一次尝试前再把 iframe 展开一次。
        try:
            sb.execute_script(_TURNSTILE_EXPAND_JS)
        except Exception:
            pass
        time.sleep(1)

    print(f"❌ Turnstile {max_attempts} 次均失败或超时")
    return False


# ============================================================
# 页面辅助
# ============================================================


def read_alert(sb) -> str:
    selectors = [
        "div.alert",
        "[role='alert']",
        ".text-destructive",
        ".text-red-500",
        ".text-red-400",
    ]
    for selector in selectors:
        try:
            for element in sb.find_elements(selector):
                text = (element.text or "").strip()
                if text:
                    return text
        except Exception:
            pass
    return ""


def parse_duration_to_minutes(value: str) -> int | None:
    if not value:
        return None

    text = value.strip()
    if text.lower() == "expired":
        return 0

    day_match = re.search(r"(\d+)d", text, re.IGNORECASE)
    hour_match = re.search(r"(\d+)h", text, re.IGNORECASE)
    minute_match = re.search(r"(\d+)m", text, re.IGNORECASE)

    if not (day_match or hour_match or minute_match):
        return None

    days = int(day_match.group(1)) if day_match else 0
    hours = int(hour_match.group(1)) if hour_match else 0
    minutes = int(minute_match.group(1)) if minute_match else 0
    return days * 1440 + hours * 60 + minutes


def extract_remaining_minutes(sb) -> int | None:
    try:
        page_source = sb.get_page_source()
    except Exception as exc:
        print(f"⚠️ 获取页面源码失败: {exc}")
        return None

    if re.search(
        r"Expiry\s*\(Next Renewal\).*?Expired",
        page_source,
        re.IGNORECASE | re.DOTALL,
    ):
        print("⚠️ 检测到服务器已过期（Expired）")
        return 0

    span_match = re.search(
        r"Expiry\s*\(Next Renewal\).*?<span[^>]*>\s*"
        r"((?:\d+d\s*)?(?:\d+h\s*)?(?:\d+m\s*)?)\s*</span>",
        page_source,
        re.IGNORECASE | re.DOTALL,
    )
    if span_match:
        minutes = parse_duration_to_minutes(span_match.group(1))
        if minutes is not None:
            print(f"✅ 成功提取时间（HTML）: {minutes} 分钟")
            return minutes

    # raw string：不会再出现 invalid escape sequence '\d' 警告。
    js_extract = r"""
(function () {
    var spans = document.querySelectorAll(
        'span.font-medium.text-foreground, span.text-foreground, span'
    );

    for (var i = 0; i < spans.length; i++) {
        var text = (spans[i].textContent || '').trim();
        if (/^(?:\d+d\s*)?(?:\d+h\s*)?(?:\d+m\s*)$/.test(text) && /\d+[dhm]/.test(text)) {
            return text;
        }
    }

    var bodyText = (document.body && document.body.innerText) || '';
    if (/Expiry\s*\(Next Renewal\)[\s\S]{0,300}Expired/i.test(bodyText)) {
        return 'Expired';
    }

    return null;
})();
"""

    try:
        value = sb.execute_script(js_extract)
        minutes = parse_duration_to_minutes(value or "")
        if minutes is not None:
            print(f"✅ 成功提取时间（JavaScript）: {minutes} 分钟")
            return minutes
    except Exception as exc:
        print(f"⚠️ JavaScript 提取剩余时间失败: {exc}")

    print("⚠️ 所有剩余时间提取方法均失败")
    return None


def format_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes == 0:
        return "Expired"
    days = minutes // 1440
    hours = (minutes % 1440) // 60
    mins = minutes % 60
    return f"{days}d {hours}h {mins}m"


# ============================================================
# 登录
# ============================================================


def login(sb) -> bool:
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 登录")
    print("#" * 25)
    print(f"🌐 打开登录页面: {LOGIN_URL}")

    try:
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=8)
    except Exception as exc:
        print(f"❌ 打开登录页面失败: {exc}")
        # 页面甚至没打开时截图可能失败，但仍尝试发通知。
        notify_login_failure(sb, f"打开登录页失败: {exc}", "login_open_fail.png")
        return False

    print("⏳ 等待登录表单加载……")
    try:
        sb.wait_for_element_visible(EMAIL_SELECTOR, timeout=30)
        sb.wait_for_element_visible(PASSWORD_SELECTOR, timeout=30)
        print("✅ 登录表单加载成功")
    except Exception as exc:
        print(f"❌ 登录表单未加载成功: {exc}")
        notify_login_failure(
            sb,
            f"登录表单未加载成功: {exc}",
            "login_form_fail.png",
        )
        return False

    # --------------------------------------------------------
    # 关键修改：表单出现后，任何下一步操作之前，强制等待 20~30 秒。
    # 默认固定 25 秒，可通过 LOGIN_STABILIZE_SECONDS 修改。
    # --------------------------------------------------------
    print(
        f"🕒 登录表单已就绪，强制等待 {LOGIN_STABILIZE_SECONDS} 秒，"
        "让页面与验证组件完整加载……"
    )
    for remaining in range(LOGIN_STABILIZE_SECONDS, 0, -5):
        print(f"   ⏳ 还需等待约 {remaining} 秒...")
        time.sleep(min(5, remaining))
    print("✅ 强制等待结束，开始下一步操作")

    # Cookie 同意按钮。
    try:
        for button in sb.find_elements("button"):
            text = (button.text or "").strip().lower()
            if text in {"accept", "accept all", "同意", "接受", "allow all"}:
                button.click()
                print("🍪 已处理 Cookie 同意按钮")
                time.sleep(1)
                break
    except Exception:
        pass

    try:
        print(f"📧 填写邮箱 ({EMAIL_SELECTOR})……")
        sb.update_text(EMAIL_SELECTOR, EMAIL)
        time.sleep(0.5)

        print(f"🔑 填写密码 ({PASSWORD_SELECTOR})……")
        sb.update_text(PASSWORD_SELECTOR, PASSWORD)
        time.sleep(1)
    except Exception as exc:
        print(f"❌ 填写登录信息失败: {exc}")
        notify_login_failure(
            sb,
            f"填写账号密码失败: {exc}",
            "login_input_fail.png",
        )
        return False

    # 登录前先检查挑战是否存在。
    if turnstile_exists(sb):
        print("🛡️ 检测到 Turnstile 验证，开始处理...")
        if not handle_turnstile(sb, max_attempts=3, timeout_per_attempt=20):
            print("❌ 登录阶段 Turnstile 未通过")
            notify_login_failure(
                sb,
                "登录阶段 Turnstile 三次均未通过或超时",
                "login_turnstile_fail.png",
            )
            return False
    else:
        print("ℹ️ 登录提交前未检测到 Turnstile")

    print("🖱️ 敲击回车提交表单...")
    try:
        sb.press_keys(PASSWORD_SELECTOR, "\n")
    except Exception as exc:
        print(f"⚠️ 回车提交失败，尝试点击 Login 按钮: {exc}")
        try:
            clicked = sb.execute_script(
                r"""
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
"""
            )
            if not clicked:
                raise RuntimeError("找不到 Login/Sign in 按钮")
        except Exception as click_exc:
            notify_login_failure(
                sb,
                f"无法提交登录表单: {click_exc}",
                "login_submit_fail.png",
            )
            return False

    print("⏳ 等待登录结果……")
    login_paths = {"/auth/login", "/login"}

    for second in range(1, 46):
        time.sleep(1)

        current_url = safe_current_url(sb)
        path = ""
        try:
            path = urlparse(current_url).path.rstrip("/").lower()
        except Exception:
            pass

        if path and path not in login_paths:
            print("✅ 登录成功！")
            print(f"📄 当前 URL: {current_url}")
            print(f"📄 标题: {safe_title(sb)}")
            return True

        # 有些页面 URL 还没变，但登录表单已经消失。
        try:
            if (
                not sb.is_element_present(EMAIL_SELECTOR)
                and not sb.is_element_present(PASSWORD_SELECTOR)
            ):
                print("✅ 登录表单已消失，判定登录成功")
                return True
        except Exception:
            pass

        alert_text = read_alert(sb)
        if alert_text:
            lowered = alert_text.lower()
            print(f"📩 页面提示: {alert_text[:300]}")
            bad_keywords = (
                "invalid credential",
                "invalid credentials",
                "incorrect",
                "wrong password",
                "invalid password",
                "login failed",
            )
            if any(keyword in lowered for keyword in bad_keywords):
                print("❌ 页面明确提示账号或密码错误")
                notify_login_failure(
                    sb,
                    f"页面提示账号或密码错误: {alert_text}",
                    "login_bad_credentials.png",
                )
                return False

        # 如果提交以后才动态出现 Turnstile，只记录并尝试一次处理流程。
        # 这样避免“页面刚出现验证组件，脚本却只顾等 URL”的情况。
        if second in {3, 6, 10} and turnstile_exists(sb) and not turnstile_solved(sb):
            print("🛡️ 登录提交后检测到新的 Turnstile 验证")
            if handle_turnstile(sb, max_attempts=2, timeout_per_attempt=20):
                print("✅ 提交后 Turnstile 已通过，再提交一次登录表单")
                try:
                    sb.press_keys(PASSWORD_SELECTOR, "\n")
                except Exception:
                    pass
            else:
                print("⚠️ 提交后 Turnstile 仍未通过，继续等待最终结果")

        if second % 10 == 0:
            print(f"   ⏳ 已等待登录结果 {second} 秒...")

    print("❌ 登录超时（45 秒）")
    notify_login_failure(
        sb,
        "登录 45 秒仍未跳出登录页",
        "login_timeout.png",
    )
    return False


# ============================================================
# 获取服务器 ID
# ============================================================


def get_server_ids(sb) -> list[str]:
    print("🔍 正在提取服务器 ID 列表...")
    time.sleep(5)

    ids: list[str] = []

    # 只从服务器链接提取 ID。旧写法先用正则 ID:\s*(\d+) 扫整个页面源码，
    # 会先命中首页 Personal Information 里的账号 ID（例如 24041），
    # 把账号 ID 当成服务器 ID：打开 /server?id=24041 被面板重定向回
    # 服务器列表页，页面上没有 Renew Server 按钮，报“无法点击”。
    # 2026-11-10 在真实面板核实：账号下服务器为 #19061，按钮存在且可用。
    try:
        for element in sb.find_elements("a[href*='/server?id=']"):
            href = element.get_attribute("href") or ""
            match = re.search(r"[?&]id=(\d+)", href)
            if match:
                ids.append(match.group(1))
    except Exception as exc:
        print(f"⚠️ 链接提取服务器 ID 失败: {exc}")

    if not ids:
        current_url = safe_current_url(sb)
        match = re.search(r"[?&]id=(\d+)", current_url)
        if match:
            ids.append(match.group(1))

    # 保持顺序去重。
    unique_ids = list(dict.fromkeys(ids))
    if unique_ids:
        print(f"✅ 找到 {len(unique_ids)} 个服务器 ID: {unique_ids}")
    else:
        print("❌ 未能提取到任何服务器 ID")

    return unique_ids


# ============================================================
# 续期单个服务器
# ============================================================


def click_renew_button(sb) -> bool:
    """点击 Renew Server 按钮（JS 点击）。

    2026-11-10 多轮实测：JS 点击能正常打开续期验证弹窗，足够了。
    WebDriver 真实点击试过两条路都走不通：XPath 口径找不到按钮；
    用 JS 返回元素本身时 UC/CDP 的 execute_script 无法回传 DOM
    元素（cannot unpack non-iterable NoneType），故不再尝试。
    """
    click_script = r"""
(function () {
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        var text = (buttons[i].textContent || '').trim();
        if (text === 'Renew Server') {
            if (buttons[i].disabled) continue;
            buttons[i].scrollIntoView({behavior: 'smooth', block: 'center'});
            buttons[i].click();
            return true;
        }
    }
    return false;
})();
"""

    try:
        clicked = bool(sb.execute_script(click_script))
        if clicked:
            print("🖱️ Renew Server 已 JS 点击")
        return clicked
    except Exception as exc:
        print(f"⚠️ JavaScript 点击 Renew Server 失败: {exc}")
        return False


def renew_button_state(sb) -> str:
    """读取详情页上续期按钮的状态。

    - "cooldown"：刚续过、还在 30 分钟冷却里——按钮变成禁用的
      'Renew Cooldown (30m)'，文字不再是 'Renew Server'，
      click_renew_button 必然找不到按钮；页面也可能有
      'Server renewed recently' 提示。（2026-11-10 实测：手动续期
      约 24 分钟后的一轮自动运行正死在这里，被误报成点击失败。）
    - "ready"：存在未禁用的 'Renew Server' 按钮，可以点。
    - "missing"：两种按钮都没找到，交给后续点击逻辑报错截图。
    """
    try:
        return str(
            sb.execute_script(
                """
(function () {
    var bodyText = document.body ? (document.body.innerText || '') : '';
    var buttons = document.querySelectorAll('button');
    var hasReady = false;
    for (var i = 0; i < buttons.length; i++) {
        var text = (buttons[i].textContent || '').trim();
        if (text.includes('Renew Cooldown')) return 'cooldown';
        if (text === 'Renew Server' && !buttons[i].disabled) hasReady = true;
    }
    if (bodyText.includes('Server renewed recently')) return 'cooldown';
    return hasReady ? 'ready' : 'missing';
})();
                """
            )
        )
    except Exception:
        return "missing"


def dump_renew_area(sb) -> str:
    """点击 Renew 后弹窗未出现时的诊断：汇总页面上续期相关元素的状态，
    区分『弹窗没开』『按钮点击后变样』『弹窗开了但验证 iframe 没加载』。"""
    try:
        return str(
            sb.execute_script(
                """
(function () {
    var out = [];
    document.querySelectorAll('button').forEach(function (b) {
        var t = (b.textContent || '').trim();
        if (/renew/i.test(t)) out.push('button[' + t + '] disabled=' + b.disabled);
    });
    document.querySelectorAll('[class*="modal"], [role="dialog"]').forEach(function (m) {
        var r = m.getBoundingClientRect();
        if (r.width > 0) out.push('dialog visible: ' + (m.innerText || '').slice(0, 120).replace(/\\n/g, ' '));
    });
    var bt = document.body ? (document.body.innerText || '') : '';
    ['Processing', 'captcha', 'verification', 'Renew Cooldown', 'renewed recently'].forEach(function (k) {
        if (bt.toLowerCase().includes(k.toLowerCase())) out.push('page contains: ' + k);
    });
    return out.join(' | ') || '(无续期相关元素)';
})();
                """
            )
        )
    except Exception as exc:
        return f"(诊断失败: {exc})"


def renew_cooldown_seen(sb) -> bool:
    """续期成功后面板会出现 'Server renewed recently' 提示或
    'Renew Cooldown' 禁用按钮，这是比时间差更可靠的成功信号。"""
    try:
        return bool(
            sb.execute_script(
                """
(function () {
    var t = document.body ? document.body.innerText : '';
    return t.includes('Renew Cooldown') || t.includes('Server renewed recently');
})();
                """
            )
        )
    except Exception:
        return False


def _renew_modal_state(sb) -> dict:
    """读取续期验证弹窗的当前状态（脚本用 IIFE 包裹：UC/CDP 下
    execute_script 顶层 return 会 SyntaxError，旧版因此恒失效）。"""
    try:
        return sb.execute_script(
            """
(function () {
    var bt = document.body ? (document.body.innerText || '') : '';
    function vis(sel) {
        var els = document.querySelectorAll(sel);
        for (var i = 0; i < els.length; i++) {
            var r = els[i].getBoundingClientRect();
            if (r.width > 20 && r.height > 10) return true;
        }
        return false;
    }
    function tok(sel) {
        var els = document.querySelectorAll(sel);
        for (var i = 0; i < els.length; i++) {
            if (els[i].value && els[i].value.length > 20) return true;
        }
        return false;
    }
    return {
        modalText: bt.includes('verification below to renew'),
        loading: bt.includes('Loading Cloudflare verification'),
        tsIframe: vis('iframe[src*="challenges.cloudflare"]'),
        rcAnchor: vis('iframe[src*="recaptcha/api2/anchor"]'),
        rcChallenge: vis('iframe[src*="recaptcha/api2/bframe"]'),
        tsToken: tok('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'),
        rcToken: tok('textarea[name="g-recaptcha-response"], #g-recaptcha-response'),
        cooldown: bt.includes('Renew Cooldown') || bt.includes('Server renewed recently')
    };
})();
            """
        ) or {}
    except Exception:
        return {}


def handle_renew_verification(sb, timeout: int = 300) -> str:
    """处理点击 Renew Server 后弹出的验证模态框。

    2026-11-10 16:21 的诊断截图实证：弹窗会被点击正常打开，里面是
    Cloudflare 验证（Turnstile），文案 'Please complete the Cloudflare
    verification below to renew your server.'，截图时组件还停在
    'Loading Cloudflare verification...'。此前几轮失败的真正原因：
    旧处理只按 Google reCAPTCHA 的特征找弹窗，对 Cloudflare 弹窗
    完全无感；且旧版几个 execute_script 没用 IIFE 包裹，顶层 return
    在 UC/CDP 下直接 SyntaxError 被异常吞掉，按钮状态恒读 'missing'、
    冷却信号恒为 False。点击本身从头到尾没有问题。

    返回值：
    - "passed"：验证通过（token 生成且弹窗关闭，或页面出现冷却信号）
    - "challenge"：出现交互挑战（reCAPTCHA 图片题）或验证组件一直加载不出来
    - "none"：没检测到验证弹窗
    - "timeout"：弹窗在但等待超时（交给调用方按时间/冷却信号兜底判定）
    """
    # 阶段 1：等弹窗出现（最多 30 秒）。
    modal_seen = False
    st = {}
    for _ in range(30):
        st = _renew_modal_state(sb)
        if st.get("modalText") or st.get("tsIframe") or st.get("rcAnchor"):
            modal_seen = True
            break
        if st.get("cooldown"):
            print("✅ 点击后直接进入冷却（已续期，无验证弹窗）")
            return "passed"
        time.sleep(1)
    if not modal_seen:
        print("ℹ️ 点击后未检测到续期验证弹窗")
        return "none"

    provider = (
        "Cloudflare Turnstile"
        if (st.get("tsIframe") or st.get("loading"))
        else "Google reCAPTCHA"
    )
    print(f"🛡️ 续期验证弹窗已打开（{provider}），等待组件加载...")

    # 阶段 2：等组件加载出来（最多 150 秒）后点击，轮询结果。
    clicked = 0
    last_click = 0.0
    challenge_since = None
    started = time.time()
    while time.time() - started < timeout:
        st = _renew_modal_state(sb)
        if st.get("cooldown"):
            print("✅ 续期验证已通过（检测到冷却/已续期提示）")
            return "passed"
        token = st.get("tsToken") or st.get("rcToken")
        modal_open = st.get("modalText") or st.get("tsIframe") or st.get("rcAnchor")
        if token and not modal_open:
            print("✅ 续期验证已通过（token 已生成、弹窗已关闭）")
            return "passed"
        if st.get("rcChallenge"):
            if challenge_since is None:
                challenge_since = time.time()
            elif time.time() - challenge_since >= 30:
                print("⚠️ reCAPTCHA 弹出图片题，自动化无法完成")
                return "challenge"
        # 组件就绪的判定：iframe 可见，或弹窗里的 'Loading Cloudflare
        # verification...' 字样已消失（组件渲染完该字样就没了）。
        # 2026-11-10 16:32 实测：该组件在这个环境里要 60 秒以上才
        # 加载出来——失败截图拍到的正是刚渲染好的复选框，所以等待
        # 上限给到 150 秒。
        widget_ready = (
            st.get("tsIframe")
            or st.get("rcAnchor")
            or (st.get("modalText") and not st.get("loading"))
        )
        if (
            widget_ready
            and not token
            and clicked < 4
            and time.time() - last_click >= 20
        ):
            try:
                sb.uc_gui_click_captcha()
                clicked += 1
                last_click = time.time()
                print(f"🖱️ 已点击验证组件（第 {clicked} 次）")
            except Exception as exc:
                print(f"⚠️ uc_gui_click_captcha 失败: {exc}")
        if not widget_ready and time.time() - started > 150:
            print("⚠️ 验证组件 150 秒仍未加载出来（一直 Loading）")
            return "challenge"
        time.sleep(3)

    if renew_cooldown_seen(sb):
        return "passed"
    print("⚠️ 等待续期验证结果超时")
    return "timeout"


def renew_one_server_by_id(sb, server_id: str, index: int) -> dict:
    result = {
        "index": index,
        "server_id": server_id,
        "status": "unknown",
        "detail": "",
    }

    detail_url = f"{BASE_URL}/server?id={server_id}"

    try:
        print(f"\n🔄 正在处理第 {index + 1} 个服务器: ID={server_id}")
        print(f"🌐 打开详情页: {detail_url}")
        sb.get(detail_url)

        print("⏳ 等待页面关键内容加载...")
        try:
            sb.wait_for_text("Server last renewed", timeout=15)
            print("✅ 检测到 'Server last renewed' 文字")
        except Exception:
            try:
                sb.wait_for_text("Expiry (Next Renewal)", timeout=10)
                print("✅ 检测到 'Expiry (Next Renewal)' 文字")
            except Exception:
                print("⚠️ 未检测到预期文字，但继续尝试")

        time.sleep(3)
        current_url = safe_current_url(sb)
        if "/server" not in current_url.lower():
            result["status"] = "failed"
            result["detail"] = f"未进入详情页，当前 URL={current_url}"
            save_screenshot(sb, f"server_page_fail_{server_id}.png")
            return result

        old_minutes = extract_remaining_minutes(sb)
        print(f"📅 原始剩余时间: {format_minutes(old_minutes)}")

        if renew_button_state(sb) == "cooldown":
            result["status"] = "skipped"
            result["detail"] = (
                "续期冷却中（刚续过，按钮为 Renew Cooldown）：剩余 "
                f"{format_minutes(old_minutes)}，本轮无需重复续期"
            )
            print(f"⏭️ {result['detail']}")
            return result

        print("🖱️ 尝试点击 Renew Server 按钮...")
        if not click_renew_button(sb):
            result["status"] = "error"
            result["detail"] = "无法点击 Renew Server 按钮"
            path = f"renew_click_fail_{server_id}.png"
            save_screenshot(sb, path)
            send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], path)
            return result

        print("✅ Renew Server 已点击")
        rc_result = handle_renew_verification(sb)
        if rc_result == "none":
            dump = dump_renew_area(sb)
            if dump:
                print(f"🔎 点击后页面诊断: {dump}")
            diag_path = f"renew_after_click_{server_id}.png"
            save_screenshot(sb, diag_path)
            send_tg_message("🔎", f"服务器 {server_id} 点击 Renew 后未见验证弹窗", "已截图诊断，页面状态见运行日志", diag_path)
            state_after = renew_button_state(sb)
            if state_after == "cooldown":
                rc_result = "passed"
                print("✅ 点击后已进入冷却（已续期，本次未弹验证）")
            elif state_after == "missing":
                print("⚠️ 点击后按钮消失/变样，等待 10 秒再确认状态...")
                time.sleep(10)
                if renew_button_state(sb) == "cooldown" or renew_cooldown_seen(sb):
                    rc_result = "passed"
                    print("✅ 延迟确认：已进入冷却（已续期）")
            elif state_after == "ready":
                print("⚠️ 点击后未出现验证弹窗且按钮仍可点，疑似点击未生效，重试一次点击...")
                if click_renew_button(sb):
                    rc_result = handle_renew_verification(sb)
                if rc_result == "none" and renew_button_state(sb) == "ready":
                    result["status"] = "error"
                    result["detail"] = "点击 Renew Server 后未弹出 reCAPTCHA 验证弹窗（疑似点击未生效），续期未发生"
                    path = f"renew_no_modal_{server_id}.png"
                    save_screenshot(sb, path)
                    send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], path)
                    return result
        if rc_result == "challenge":
            result["status"] = "error"
            result["detail"] = "续期弹窗的验证无法自动完成（图片题或组件加载失败），需人工在面板点 Renew 过验证"
            path = f"renew_recaptcha_{server_id}.png"
            save_screenshot(sb, path)
            send_tg_message("❌", f"服务器 {server_id} 续期失败", result["detail"], path)
            return result

        print("⏳ 等待 5 秒后检查时间变化...")
        time.sleep(5)

        quick_minutes = extract_remaining_minutes(sb)
        if renew_cooldown_seen(sb):
            result["status"] = "success"
            result["detail"] = (
                f"续期成功（页面出现已续期/冷却提示）：剩余 "
                f"{format_minutes(old_minutes)} -> {format_minutes(quick_minutes)}"
            )
            path = f"renew_success_{server_id}.png"
            save_screenshot(sb, path)
            send_tg_message("✅", f"服务器 {server_id} 续期成功", result["detail"], path)
            return result
        if old_minutes is not None and quick_minutes is not None:
            delta = quick_minutes - old_minutes
            if delta > 1000 or (old_minutes == 0 and quick_minutes > 0):
                result["status"] = "success"
                result["detail"] = (
                    f"续期成功：{format_minutes(old_minutes)} -> "
                    f"{format_minutes(quick_minutes)}，增加 {delta} 分钟"
                )
                path = f"renew_success_{server_id}.png"
                save_screenshot(sb, path)
                send_tg_message("✅", f"服务器 {server_id} 续期成功", result["detail"], path)
                return result

        # 没有立即变化时检查验证组件（只认可见的挑战，隐藏输入框是误报）。
        if turnstile_challenge_visible(sb) and not turnstile_solved(sb):
            print("🛡️ 续期阶段检测到可见的 Turnstile 验证")
            if not handle_turnstile(sb, max_attempts=3, timeout_per_attempt=20):
                print("⚠️ 续期阶段 Turnstile 未通过，但仍继续刷新确认最终状态")

        print("⏳ 等待 5 秒并重新加载详情页确认最终状态...")
        time.sleep(5)
        sb.get(detail_url)
        try:
            sb.wait_for_text("Server last renewed", timeout=15)
        except Exception:
            pass
        time.sleep(3)

        new_minutes = extract_remaining_minutes(sb)
        print(f"📅 最终剩余时间: {format_minutes(new_minutes)}")

        alert_text = read_alert(sb)
        if alert_text:
            print(f"📩 页面提示: {alert_text[:300]}")

        if renew_cooldown_seen(sb):
            result["status"] = "success"
            result["detail"] = (
                f"续期成功（页面出现已续期/冷却提示）：剩余 "
                f"{format_minutes(old_minutes)} -> {format_minutes(new_minutes)}"
            )
        elif old_minutes is not None and new_minutes is not None:
            delta = new_minutes - old_minutes
            if delta > 1000 or (old_minutes == 0 and new_minutes > 0):
                result["status"] = "success"
                result["detail"] = (
                    f"续期成功：{format_minutes(old_minutes)} -> "
                    f"{format_minutes(new_minutes)}，增加 {delta} 分钟"
                )
            elif -10 < delta < 100:
                result["status"] = "skipped"
                result["detail"] = f"时间变化不明显：{delta} 分钟"
            else:
                result["status"] = "unknown"
                result["detail"] = f"时间变化异常：{delta} 分钟"
        elif alert_text and any(
            word in alert_text.lower() for word in ("renewed", "success", "extended")
        ):
            result["status"] = "success"
            result["detail"] = alert_text
        else:
            result["status"] = "unknown"
            result["detail"] = (
                f"无法确认续期结果，原={old_minutes}，新={new_minutes}"
            )

        screenshot_path = f"renew_result_{server_id}.png"
        save_screenshot(sb, screenshot_path)

        if result["status"] == "success":
            send_tg_message(
                "✅",
                f"服务器 {server_id} 续期成功",
                result["detail"],
                screenshot_path,
            )
        elif result["status"] == "skipped":
            send_tg_message(
                "⏭️",
                f"服务器 {server_id} 本次跳过/未变化",
                result["detail"],
                screenshot_path,
            )
        else:
            send_tg_message(
                "⚠️",
                f"服务器 {server_id} 续期状态异常",
                result["detail"],
                screenshot_path,
            )

        return result

    except Exception as exc:
        print(f"❌ 处理服务器 {server_id} 异常: {exc}")
        result["status"] = "error"
        result["detail"] = str(exc)
        path = f"renew_exception_{server_id}.png"
        save_screenshot(sb, path)
        send_tg_message(
            "❌",
            f"服务器 {server_id} 续期异常",
            result["detail"],
            path,
        )
        return result


# ============================================================
# 全部服务器续期
# ============================================================


def renew_all_servers_by_id(sb) -> list[dict]:
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 自动续期流程（通过服务器 ID）")
    print("#" * 25)

    server_ids = get_server_ids(sb)
    if not server_ids:
        path = "no_servers.png"
        save_screenshot(sb, path)
        send_tg_message("❌", "执行失败", "未获取到任何服务器 ID", path)
        return []

    print(f"📋 待续期服务器 ID 列表: {server_ids}")
    results: list[dict] = []

    for index, server_id in enumerate(server_ids):
        result = renew_one_server_by_id(sb, server_id, index)
        results.append(result)
        print(
            f"📊 第 {index + 1} 个服务器 (ID={server_id}) "
            f"续期结果: {result['status']} - {result['detail']}"
        )

    total = len(results)
    success = sum(1 for item in results if item["status"] == "success")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    failed = total - success - skipped

    summary = (
        f"续期完成：共 {total} 个服务器\n"
        f"✅ 成功: {success}\n"
        f"⏭️ 跳过: {skipped}\n"
        f"❌ 失败/未知: {failed}"
    )

    detail = "\n".join(
        f"#{item['index'] + 1} ID={item['server_id']}: "
        f"{item['status']} - {item['detail']}"
        for item in results
    )

    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)
    print(detail)
    print("=" * 50)

    return results


# ============================================================
# 主程序
# ============================================================


def main() -> None:
    print("#" * 40)
    print("   ZamPTO 自动登录续期")
    print(f"   版本: {SCRIPT_VERSION}")
    print("#" * 40)

    if not EMAIL or not PASSWORD:
        print("❌ 未配置 ZAM_PTO_EMAIL 或 ZAM_PTO_PASSWORD")
        send_tg_message("❌", "账号环境变量未配置")
        raise SystemExit(1)

    sb_kwargs = {
        "uc": True,
        "headless": False,
    }

    if IS_PROXY:
        print(f"🔗 使用 sing-box 本地代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🌐 未启用代理，使用直连")

    try:
        with SB(**sb_kwargs) as sb:
            try:
                sb.open("https://api.ip.sb/ip")
                exit_ip = sb.get_text("body").strip()
                print(f"📍 当前出口 IP: {exit_ip}")
            except Exception as exc:
                print(f"⚠️ 无法获取出口 IP: {exc}")
                if IS_PROXY:
                    send_tg_message("❌", "代理连接失败", str(exc))
                    raise SystemExit(1)

            if not login(sb):
                print("\n❌ 登录失败，终止续期操作。")
                # login() 内部已经发送带截图的 TG，避免这里重复发一条纯文字。
                raise SystemExit(1)

            print("\n🎉 登录流程成功")
            renew_all_servers_by_id(sb)

    except SystemExit:
        raise
    except Exception as exc:
        print(f"❌ 程序运行异常: {exc}")
        send_tg_message("❌", "程序运行异常", str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

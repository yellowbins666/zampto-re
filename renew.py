#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import re
import requests
from seleniumbase import SB
from datetime import datetime

# ============================================================
# 环境变量
# ============================================================

EMAIL = os.environ.get("ZAM_PTO_EMAIL", "").strip()
PASSWORD = os.environ.get("ZAM_PTO_PASSWORD", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

IS_PROXY = os.environ.get("IS_PROXY", "true").strip().lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "http://127.0.0.1:1081").strip()

BASE_URL = "https://dash.zampto.net"
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"

# ============================================================
# Telegram 通知 (支持发送图片截图)
# ============================================================

def send_tg_message(status_icon: str, status_text: str, detail: str = "", photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送喵。")
        return

    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    if "@" in EMAIL:
        name, domain = EMAIL.split("@", 1)
        masked_email = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + "****" if EMAIL else "未配置"

    text = (
        f"🇫🇷 ZamPTO 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 账户: {masked_email}\n"
        f"⏱️ 时间: {current_time}"
    )
    if detail:
        text += f"\n📝 详情: {detail[:800]}"

    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
            with open(photo_path, 'rb') as f:
                r = requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": text}, files={"photo": f}, timeout=20)
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
            
        if r.ok:
            print("📩 Telegram 消息 (含截图) 发送成功喵！")
        else:
            print(f"⚠️ Telegram 发送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"⚠️ Telegram 发送异常: {e}")

# ============================================================
# 喵酱的无敌 Cloudflare 穿透打勾模块
# ============================================================

def _turnstile_token_ready(sb) -> bool:
    try:
        token_ok = sb.execute_script("""
            var inp = document.querySelector("input[name='cf-turnstile-response']");
            return inp && inp.value && inp.value.length > 20;
        """)
        if token_ok: return True
    except Exception: pass

    try:
        success_visible = sb.execute_script("""
            var s = document.getElementById('success');
            if (!s) return false;
            var style = window.getComputedStyle(s);
            return style.display !== 'none' && style.visibility !== 'hidden';
        """)
        if success_visible: return True
    except Exception: pass
    return False

def _try_click_turnstile(sb) -> bool:
    try: sb.uc_gui_click_captcha()
    except Exception: pass

    try:
        if sb.is_element_present("iframe[src*='challenges.cloudflare']"):
            sb.switch_to_frame("iframe[src*='challenges.cloudflare']")
            sb.click("input[type='checkbox'], .cb-lb, .mark", timeout=2)
            sb.switch_to_default_content()
            return True
    except Exception:
        try: sb.switch_to_default_content()
        except Exception: pass

    try:
        sb.execute_script("""
            var ts = document.querySelector('.cf-turnstile');
            if (ts) ts.click();
        """)
    except Exception: pass
    return False

def wait_turnstile(sb, timeout: int = 60) -> bool:
    print("⏳ 强制等待 8 秒，让网页和验证码彻底加载出来喵...")
    time.sleep(8)
    
    # 确认是否真的有验证码
    has_cf = False
    try:
        has_cf = sb.execute_script("""
            return !!document.querySelector('.cf-turnstile') || 
                   !!document.querySelector('iframe[src*="challenges.cloudflare"]') ||
                   !!document.querySelector('input[name="cf-turnstile-response"]');
        """)
    except Exception: pass
        
    if not has_cf:
        print("ℹ️ 确认页面没有 Turnstile 验证码框，直接跳过喵！")
        return True

    print("🔍 发现验证码，正在耐心死磕打勾喵...")
    try:
        sb.execute_script("""
            var ts = document.querySelector('.cf-turnstile') || document.querySelector('iframe[src*="challenges.cloudflare"]');
            if (ts) ts.scrollIntoView({block:'center'});
        """)
    except Exception: pass

    start = time.time()
    last_click = 0

    while time.time() - start < timeout:
        if _turnstile_token_ready(sb):
            print("✅ Turnstile 绿勾验证完成喵！")
            time.sleep(2) 
            return True

        now = time.time()
        if now - last_click >= 4:
            print("🖱️ 尝试戳一下中间的框框...")
            _try_click_turnstile(sb)
            last_click = now

        time.sleep(1)

    print("❌ Turnstile 打勾超时！")
    return _turnstile_token_ready(sb)

# ============================================================
# 辅助函数
# ============================================================

def read_alert(sb) -> str:
    try:
        alerts = sb.find_elements("div.alert")
        for alert in alerts:
            text = (alert.text or "").strip()
            if text: return text
    except Exception: pass
    return ""

def extract_remaining_minutes(sb):
    try:
        page_text = sb.get_page_source()
        if re.search(r'Expiry\s*\(Next Renewal\).*?Expired', page_text, re.IGNORECASE | re.DOTALL):
            print("⚠️ 检测到服务器已过期（Expired）")
            return 0
        
        span_match = re.search(
            r'Expiry\s*\(Next Renewal\).*?<span[^>]*>((?:\d+d\s*)?(?:\d+h\s*)?(?:\d+m\s*)?)</span>',
            page_text, re.IGNORECASE | re.DOTALL
        )
        
        if span_match:
            time_str = span_match.group(1).strip()
            days = hours = minutes = 0
            d_match = re.search(r'(\d+)d', time_str)
            h_match = re.search(r'(\d+)h', time_str)
            m_match = re.search(r'(\d+)m', time_str)
            if d_match: days = int(d_match.group(1))
            if h_match: hours = int(h_match.group(1))
            if m_match: minutes = int(m_match.group(1))
            total_minutes = days * 24 * 60 + hours * 60 + minutes
            if total_minutes > 0:
                return total_minutes
        
        # 规避 Python 3.12 语法的正则表达式
        js_extract = r"""
        (function() {
            var spans = document.querySelectorAll('span.font-medium.text-foreground, span.text-foreground');
            for (var i = 0; i < spans.length; i++) {
                var text = spans[i].textContent.trim();
                if (/\d+[dhm]/.test(text)) { return text; }
            }
            var expiredSpans = document.querySelectorAll('span.text-red-400, span.font-medium.text-red-400');
            for (var i = 0; i < expiredSpans.length; i++) {
                if (expiredSpans[i].textContent.trim().toLowerCase() === 'expired') { return 'Expired'; }
            }
            return null;
        })();
        """
        try:
            time_text = sb.execute_script(js_extract)
            if time_text:
                if time_text.lower() == 'expired':
                    return 0
                days = hours = minutes = 0
                d_match = re.search(r'(\d+)d', time_text)
                h_match = re.search(r'(\d+)h', time_text)
                m_match = re.search(r'(\d+)m', time_text)
                if d_match: days = int(d_match.group(1))
                if h_match: hours = int(h_match.group(1))
                if m_match: minutes = int(m_match.group(1))
                total_minutes = days * 24 * 60 + hours * 60 + minutes
                if total_minutes > 0:
                    return total_minutes
        except Exception: pass
        return None
    except Exception: return None

# ============================================================
# 登录
# ============================================================

def login(sb) -> bool:
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 登录")
    print("#" * 25)

    login_url = f"{BASE_URL}/auth/login"
    print(f"🌐 打开登录页面: {login_url}")

    try:
        sb.uc_open_with_reconnect(login_url, reconnect_time=8)
    except Exception as exc:
        print(f"⚠️ 打开登录页面失败: {exc}")
        return False

    print("⏳ 等待登录表单加载……")
    try:
        sb.wait_for_element(EMAIL_SELECTOR, timeout=30)
        sb.wait_for_element(PASSWORD_SELECTOR, timeout=30)
        print("✅ 登录表单加载成功")
    except Exception as exc:
        print(f"❌ 登录表单未加载成功: {exc}")
        sb.save_screenshot("login_form_fail.png")
        send_tg_message("❌", "登录页面加载失败", photo_path="login_form_fail.png")
        return False

    print(f"📧 填写邮箱 ({EMAIL_SELECTOR})……")
    sb.update_text(EMAIL_SELECTOR, EMAIL)
    print(f"🔑 填写密码 ({PASSWORD_SELECTOR})……")
    sb.update_text(PASSWORD_SELECTOR, PASSWORD)

    # 🚨 在这里调用喵酱的打勾模块，绝对不抢跑！ 🚨
    if not wait_turnstile(sb, timeout=60):
        print("❌ 登录界面的 Turnstile 验证未通过，尝试强行提交！")

    print("🖱️ 点击 Login 按钮...")
    try:
        # 使用 JS 稳稳地点击登录按钮，不用回车键，防止没触发验证
        sb.execute_script("""
            var btns = document.querySelectorAll('button');
            for(var i=0; i<btns.length; i++){
                if(btns[i].innerText.trim() === 'Login'){
                    btns[i].click();
                    return;
                }
            }
            document.querySelector('#password').form.submit();
        """)
    except Exception:
        sb.press_keys(PASSWORD_SELECTOR, '\n')

    print("⏳ 等待登录结果……")
    login_paths = {"/auth/login", "/login"}
    for i in range(30):
        time.sleep(1)
        current_url = sb.get_current_url()
        normalized = current_url.split("?", 1)[0].rstrip("/").lower()
        if "://" in normalized:
            from urllib.parse import urlparse
            normalized = urlparse(normalized).path.rstrip("/").lower()

        alert_text = read_alert(sb)
        if alert_text:
            lowered = alert_text.lower()
            if any(kw in lowered for kw in ("invalid", "incorrect", "wrong password", "invalid credentials", "security verification")):
                print("❌ 账号/密码错误，或安全验证失败！")
                sb.save_screenshot("login_failed.png")
                send_tg_message("❌", "登录被拒绝 (密码错误或CF拦截)", f"提示: {alert_text}", "login_failed.png")
                return False

        if normalized not in login_paths:
            print("✅ 登录成功！")
            return True

        if not sb.is_element_present(EMAIL_SELECTOR) and not sb.is_element_present(PASSWORD_SELECTOR):
            print("✅ 登录表单已消失，判定登录成功")
            return True

    print("❌ 登录超时（30秒）")
    sb.save_screenshot("login_timeout.png")
    send_tg_message("❌", "登录响应超时", photo_path="login_timeout.png")
    return False

# ============================================================
# 获取服务器 ID 列表
# ============================================================

def get_server_ids(sb) -> list:
    print("🔍 正在提取服务器 ID 列表...")
    time.sleep(5)
    server_ids = []

    try:
        page_text = sb.get_page_source()
        pattern = r'ID:\s*(\d+)'
        matches = re.findall(pattern, page_text)
        if matches:
            server_ids = list(set(matches))
            print(f"✅ 找到 {len(server_ids)} 个服务器 ID: {server_ids}")
            return server_ids
    except Exception: pass
    return []

# ============================================================
# 续期单个服务器
# ============================================================

def renew_one_server_by_id(sb, server_id, index) -> dict:
    result = {
        "index": index,
        "server_id": server_id,
        "server_name": f"Server-{server_id}",
        "status": "unknown",
        "detail": ""
    }

    try:
        detail_url = f"{BASE_URL}/server?id={server_id}"
        print(f"\n🔄 正在处理第 {index+1} 个服务器: ID={server_id}")
        sb.get(detail_url)

        print("⏳ 等待页面加载...")
        time.sleep(5)
        
        current_url = sb.get_current_url()
        if "server" not in current_url.lower():
            result["status"] = "failed"
            result["detail"] = "未进入详情页"
            return result

        old_minutes = extract_remaining_minutes(sb)
        if old_minutes is not None:
            if old_minutes == 0: print(f"📅 原始状态: 已过期（Expired）")
            else: print(f"📅 原始剩余时间: {old_minutes} 分钟")

        print("🖱️ 尝试点击 Renew Server 按钮...")
        click_script = r"""
        (function() {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.trim() === 'Renew Server') {
                    buttons[i].scrollIntoView({behavior: 'smooth', block: 'center'});
                    buttons[i].removeAttribute("disabled");
                    buttons[i].click();
                    return 'success';
                }
            }
            return 'not_found';
        })();
        """
        try:
            if sb.execute_script(click_script) == 'success':
                print("✅ 成功点击 Renew 按钮喵！")
            else:
                result["status"] = "error"
                result["detail"] = "找不到 Renew Server 按钮"
                return result
        except Exception as e: 
            result["status"] = "error"
            result["detail"] = f"点击失败: {e}"
            return result

        # 🚨 ZamPTO 续期弹窗如果也有 CF，这里会自动处理 🚨
        wait_turnstile(sb, timeout=60)

        print("⏳ 正在等待 10 秒钟，让服务器消化加时请求...")
        time.sleep(10)

        print("🔗 重新加载详情页获取最终状态...")
        sb.get(detail_url)
        time.sleep(5)

        new_minutes = extract_remaining_minutes(sb)
        
        if old_minutes is not None and new_minutes is not None:
            time_change = new_minutes - old_minutes
            if time_change > 1000:
                result["status"] = "success"
                result["detail"] = f"成功续期！时间增加 {time_change} 分钟喵！"
            elif old_minutes == 0 and new_minutes > 0:
                result["status"] = "success"
                result["detail"] = f"成功从过期状态恢复喵！"
            elif -10 < time_change < 100:
                result["status"] = "skipped"
                result["detail"] = f"时间没有明显变化喵"
            else:
                result["status"] = "unknown"
                result["detail"] = f"时间变化异常"
        else:
            result["status"] = "unknown"
            result["detail"] = "无法读取最终剩余时间"
            
        # 截图留证
        screenshot_path = f"renew_result_{server_id}.png"
        sb.save_screenshot(screenshot_path)
        
        # 逐个服务器发送带有截图的 TG 通知
        if result["status"] == "success":
            send_tg_message("🎉", f"服务器 {server_id} 续期成功！", result["detail"], screenshot_path)
        else:
            send_tg_message("⚠️", f"服务器 {server_id} 续期状态异常", result["detail"], screenshot_path)
            
        return result
        
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
        return result

# ============================================================
# 主程序
# ============================================================

def renew_all_servers_by_id(sb):
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 自动续期")
    print("#" * 25)

    server_ids = get_server_ids(sb)
    if not server_ids:
        print("❌ 未获取到任何服务器 ID")
        sb.save_screenshot("no_servers.png")
        send_tg_message("❌", "执行失败", "未在页面中提取到任何服务器 ID喵！", "no_servers.png")
        return

    for idx, server_id in enumerate(server_ids):
        renew_one_server_by_id(sb, server_id, idx)
        
    print("\n🎉 所有服务器处理完毕喵！")

def main():
    print("#" * 40)
    print("   ZamPTO 自动续期 (终极截图推送版)")
    print("#" * 40)

    if not EMAIL or not PASSWORD:
        print("❌ 未配置账号")
        raise SystemExit(1)

    sb_kwargs = {"uc": True, "headless": False}
    if IS_PROXY:
        sb_kwargs["proxy"] = PROXY_SERVER

    try:
        with SB(**sb_kwargs) as sb:
            try:
                sb.open("https://api.ip.sb/ip")
                print(f"📍 当前出口 IP: {sb.get_text('body').strip()}")
            except Exception: pass

            if login(sb):
                print("\n🎉 登录流程成功，开始干活！")
                renew_all_servers_by_id(sb)
            else:
                print("\n❌ 登录失败，终止续期操作。")
                raise SystemExit(1)
    except Exception as exc:
        print(f"❌ 程序运行异常: {exc}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()

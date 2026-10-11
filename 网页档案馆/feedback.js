// 问题反馈：点击页面上任意 data-feedback 元素 → 弹出反馈面板
// 「调起邮箱客户端发送」= 构造 mailto: 链接（subject/body 已用 encodeURIComponent 编码，兼容各邮件客户端）
(function () {
  const EMAIL = "hong071013@qq.com";
  const SUBJECT = "拾光档案·问题反馈";
  const BODY = "我在使用「拾光档案」时遇到一个问题：\n\n- 问题描述：\n- 所在页面：\n- 操作步骤：\n- 期望结果：";

  let ov = null;

  function build() {
    const st = document.createElement("style");
    st.textContent = `
      #fb-overlay { position: fixed; inset: 0; background: rgba(20,20,20,.45); z-index: 999; display: flex; align-items: center; justify-content: center; padding: 20px; }
      #fb-overlay[hidden] { display: none; }
      .fb-box { background: #fff; border-radius: 14px; padding: 24px; width: 100%; max-width: 420px; font-family: "PingFang SC","Microsoft YaHei",system-ui,sans-serif; color:#2B2B28; }
      .fb-box h2 { font-size: 17px; margin-bottom: 10px; color: #1E4D3B; }
      .fb-box p { font-size: 13px; color: #6E6E66; margin-bottom: 14px; line-height: 1.7; }
      .fb-mail { font-size: 15px; font-weight: 700; color: #1E4D3B; background: #E7F0EB; border-radius: 8px; padding: 10px 12px; text-align: center; margin-bottom: 14px; word-break: break-all; }
      .fb-row { display: flex; gap: 10px; }
      .fb-row button { flex: 1; border: none; border-radius: 9px; padding: 11px 8px; font-size: 14px; cursor: pointer; }
      .fb-send { background: #1E4D3B; color: #fff; font-weight: 700; }
      .fb-copy { background: #F7F1DE; color: #8A6D1B; font-weight: 600; }
      .fb-close { width: 100%; margin-top: 12px; background: #EFEFF2; color: #2B2B28; border: none; border-radius: 9px; padding: 9px; font-size: 13px; cursor: pointer; }
    `;
    document.head.appendChild(st);
    ov = document.createElement("div");
    ov.id = "fb-overlay";
    ov.hidden = true;
    ov.innerHTML = `
      <div class="fb-box">
        <h2>问题反馈</h2>
        <p>感谢反馈！你的意见会直接改进「拾光档案」。反馈邮件将发送到：</p>
        <div class="fb-mail">${EMAIL}</div>
        <div class="fb-row">
          <button class="fb-send" id="fb-send">调起邮箱客户端发送</button>
          <button class="fb-copy" id="fb-copy">复制邮箱地址</button>
        </div>
        <p style="margin:12px 0 0">点击「发送」会自动打开电脑上的邮件程序，收件人、标题与反馈模板都已填好，补上问题内容即可发出。如果没有反应，说明电脑还没安装邮件客户端（如 QQ 邮箱、Outlook），复制地址后用手机或网页版邮箱发送也一样。</p>
        <button class="fb-close" id="fb-close">关闭</button>
      </div>`;
    document.body.appendChild(ov);
    ov.addEventListener("click", e => { if (e.target === ov) hide(); });
    document.getElementById("fb-send").addEventListener("click", () => {
      const href = "mailto:" + EMAIL + "?subject=" + encodeURIComponent(SUBJECT) + "&body=" + encodeURIComponent(BODY);
      window.location.href = href;
    });
    document.getElementById("fb-copy").addEventListener("click", async () => {
      const btn = document.getElementById("fb-copy");
      try {
        await navigator.clipboard.writeText(EMAIL);
        btn.textContent = "已复制";
        setTimeout(() => { btn.textContent = "复制邮箱地址"; }, 1500);
      } catch (err) {
        window.prompt("请手动复制邮箱地址：", EMAIL);
      }
    });
    document.getElementById("fb-close").addEventListener("click", hide);
  }

  function hide() { if (ov) ov.hidden = true; }
  function show() { if (!ov) build(); ov.hidden = false; }

  document.addEventListener("click", e => {
    if (e.target.closest("[data-feedback]")) show();
  });
})();
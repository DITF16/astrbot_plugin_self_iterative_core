# self_iterative_core (自迭代核心插件)

<div align="center">

<img src="https://img.shields.io/badge/AstrBot-Plugin-blue?style=flat&logo=python" alt="AstrBot Plugin">
<img src="https://img.shields.io/badge/Risk-High-red?style=flat" alt="Risk Level">
<img src="https://img.shields.io/badge/Status-Experimental-orange" alt="Status">

<p><strong>赋予 AstrBot 自我进化与代码迭代的核心能力</strong></p>

</div>

## 📖 简介 | Introduction

**Self-Iterative Core**(下文简称**自迭代核心**) 是一个为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 设计的高级插件。是可以创建、修改插件的插件。

本插件**允许 Bot 在一定范围内完善、修改、升级自身已有的功能，或者按照用户要求创造新功能，逐渐进行自我完善与进化**。它旨在探索 LLM (大语言模型) 在代码生成、自我修正及逻辑迭代方面的潜力，为您带来前所未有的智能体养成体验。

---

## ⚠️ 风险与免责声明 | Safety & Disclaimer

> [!CAUTION]
> **请在继续使用前仔细阅读以下内容。如果您无法承担潜在风险，请立即删除本插件。**

1.  **安全风险警告**：
    *   本插件虽然设计上将操作范围限制在插件目录下（相比于全局代码执行器更安全），但它仍然涉及文件系统的读写和代码的动态执行。
    *   **请勿** 在生产环境或存储敏感数据的服务器上运行此插件，除非您完全理解代码逻辑。

2.  **免责条款**：
    *   使用本插件创作的内容（包括但不限于生成的代码、文本、文件）及造成的后果（包括但不限于数据丢失、服务崩溃、被封号）均与作者无关。
    *   **用户需自行承担所有使用风险。**

---

## 🚨 市场发布规范 | Market Publishing Rules

> [!IMPORTANT]
> **严禁将未审查的 AI 生成代码直接提交至插件市场！**

如果您计划将使用本插件生成的代码/插件发布到 AstrBot 插件市场，**必须** 遵循以下规范：

1.  **必须经过人工审查**：生成的代码必须经过您的人工阅读和测试，确保无恶意逻辑、无明显 BUG。
2.  **功能稳定性**：确保插件在正常环境下运行稳定，不会导致宿主 Bot 崩溃。
3.  **符合异步规范**：AstrBot 基于异步架构，请务必检查生成的代码是否正确使用了 `async/await`，禁止在主线程中执行阻塞操作。
4.  **禁止垃圾搬运**：禁止生成后不经审查直接发布。这会给仓库维护者和代码审查人员造成巨大的无效工作压力。**违者后果自负。**

---

## 🎯 适用人群 | Target Audience

*   ✅ **推荐**：具有 Python 开发经验、熟悉 AstrBot 架构、喜欢折腾和探索 LLM 极限的极客/开发者。
*   ❌ **不推荐**：新手小白、仅寻求稳定聊天功能的普通用户。

> [!NOTE]
> **关于技术支持**：
> 本插件具有较高的使用门槛。**作者不回答任何基础性（小白）问题**（如：如何使用环境变量、为什么生成的代码有bug等）。这不仅是为了节省时间，也是为了过滤掉无法处理潜在风险的用户。

---

## 🧠 模型建议 | Model Recommendations

本插件的核心在于“迭代”与“推理”，这对大模型的逻辑思维能力提出了极高要求。

*   **推荐模型**：`gemini-3-pro`
    *   *作者强力推荐。只有具备顶尖推理能力的模型才能发挥本插件的完整功能，体验 BOT 自我升级的快感。*
*   **不推荐**：
    *   参数量较小或逻辑推理能力较弱的模型（“笨笨的大模型”）。
    *   使用此类模型可能导致插件陷入死循环、生成无效代码或完全无法工作。

---

## 📚 最佳实践 | Best Practices

为了极大提升代码生成的成功率并减少幻觉，强烈建议配合以下工具使用：

1.  **知识库增强 (RAG)**：
    *   建议为 Bot 添加 **AstrBot 开发知识库**。
    *   *原因*：LLM 默认并不清楚 AstrBot 最新的 API 变动。有了知识库，它能写出更准确、符合规范的代码。
2.  **联网能力 (Web Search)**：
    *   建议安装并开启网页搜索工具 **`astrbot-web-searcher`**。
    *   *原因*：当 LLM 遇到报错或需要使用未知的 Python 库时，它可以自主上网搜索解决方案和文档，实现自我修复。

---

## 🛠️ 安装与使用 | Installation & Usage

1.  **安装插件**：
    *   通过 **AstrBot 插件市场** 直接安装。
    *   或 **下载压缩包** 解压，将 `astrbot_plugin_self_iterative_core` 文件夹放置于 `data/plugins/` 目录下。
2.  **模型检查**：
    *   确保你当前会话正在使用高智商 LLM (如 `gemini-3-pro`)。

> [!TIP]
> **如何让修改实时生效？**
>
> 想要自迭代核心修改的功能可以实时生效，**必须开启 AstrBot 的插件热重载机制**。
> 请通过设置环境变量 `ASTRBOT_RELOAD=1` 来启动 AstrBot 主体。

---

## 💬 社区与交流 | Community

如果您是资深玩家，欢迎加入技术交流群分享您的迭代成果或魔改方案。

*   **QQ 群**：DITF16的技术交流群
*   **点击加入**：<a href="https://qm.qq.com/q/qXk7iOl9FQ">点击链接加入群聊</a>

---

<div align="center">
    <sub>Designed for AstrBot. Designed by DITF16.</sub>
</div>

# 贡献指南

我们非常欢迎你为 Prism 框架做出贡献！感谢你投入时间来让它变得更好。

在开始之前，请花几分钟阅读以下指南。

## 行为准则

本项目及其所有参与者都受我们的 [行为准则](CODE_OF_CONDUCT.md) 约束。参与本项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了一个 Bug，请先在 [GitHub Issues](https://github.com/Color-Fox/Prism/issues) 中搜索，确保该 Bug 尚未被报告。如果找不到相关问题，请创建一个新的 Issue。

在报告 Bug 时，请提供以下信息：

- **清晰的标题**：简明扼要地描述问题。
- **复现步骤**：详细说明如何重现该问题。
- **预期行为**：描述你认为应该发生什么。
- **实际行为**：描述实际发生了什么。
- **环境信息**：你的操作系统、Python 版本、Prism 版本等。
- **相关日志或截图**：如果可能，请附上相关的错误日志或截图。

### 提出功能建议

如果你有新功能或改进建议，欢迎通过 [GitHub Issues](https://github.com/Color-Fox/Prism/issues) 告诉我们。请详细描述你的想法，以及它能解决什么问题。

### 提交代码 (Pull Request)

我们非常欢迎你通过 Pull Request 提交代码。

1.  **Fork 项目**：点击项目主页右上角的 "Fork" 按钮。

2.  **克隆你的 Fork**：
    ```bash
    git clone https://github.com/你的用户名/Prism.git
    ```

3.  **创建新分支**：
    ```bash
    git checkout -b feature/your-amazing-feature
    ```

4.  **进行修改**：编写你的代码，并确保遵循项目的编码风格。

5.  **运行测试**：确保你所做的更改没有破坏任何现有功能。
    ```bash
    pytest
    ```

6.  **提交更改**：
    ```bash
    git commit -m "feat: Add some amazing feature"
    ```
    我们推荐使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范来编写提交信息。

7.  **推送到你的分支**：
    ```bash
    git push origin feature/your-amazing-feature
    ```

8.  **创建 Pull Request**：在 GitHub 上打开一个 Pull Request，将其与主项目的 `main` 分支进行比较。请在 PR 描述中清晰地说明你所做的更改。

感谢你的贡献！
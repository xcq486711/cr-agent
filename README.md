# CR-Agent

多 Agent 并行代码审查系统 — 从 PR diff 中自动发现安全、逻辑、性能问题，输出定位到行号的结构化报告。

## 核心特性

- **多维度并行审查**：Security / Logic / Performance / Style 四个 Agent 并行执行
- **自研 LLM 编排层**：retry + fallback + structured output，不依赖 LangChain
- **三阶段管线**：发现 → 交叉验证 → 置信度过滤，控制误报率
- **多语言支持**：Python / TypeScript / Go / Rust 各有独立审查 profile
- **项目级学习**：从用户反馈中自动提取规则，precision 持续提升

## 技术栈

- Python 3.12 + FastAPI
- DeepSeek (主力 LLM)
- PostgreSQL + Redis
- Docker

## 快速开始

```bash
# 安装
uv sync

# 运行审查 (Phase 1: CLI 模式)
cr-agent review --diff path/to/file.patch
```

## 文档

- [架构设计](docs/code-review-agent-design.md)
- [实施指南](docs/implementation-guide.md)

## License

MIT

# WinASR 项目日报 — AI 总结功能开发

## 今日工作概要

### 核心功能：AI 总结（从零到交付）

完成了 AI 总结功能的完整开发，支持对转录结果进行 LLM 驱动的总结/整理。

**后端（4 个文件新增，5 个文件修改）：**

- `backend/summarize.py` — 核心模块：LLM 客户端管理、预设管理、prompt 模板加载、分段处理、结果持久化
- `backend/prompts/` — 两套预设模板（播客整理润色 + 会议总结），支持热更新无需重启
- `backend/schema.py` — 新增 `SummarizeJob` 数据结构
- `backend/app.py` — 新增 6 个 API 端点（presets/创建/状态/结果/预览）
- `pyproject.toml` — 新增 `openai` + `python-dotenv` 依赖
- `run_server.py` — 启动时 `load_dotenv()` 加载 .env

**前端（1 个文件新增，4 个文件修改）：**

- `app/src/components/SummarizeModal.tsx` — 弹窗组件：预设选择、参考文件上传（拖拽+点击）、异步轮询进度、完成操作（预览/下载/重新总结）
- `app/src/components/WorkspacePage.tsx` — 集成"🤖 AI 总结"按钮
- `app/src/types/index.ts` — 新增类型定义
- `app/src/services/api.ts` — 新增 4 个 API 函数
- `app/src/stores/editorStore.ts` — 修复 Segment 类型缺失字段

**配置：**

- `.env.example` — WINASR_LLM_* 配置模板（API_BASE/KEY/MODEL/MAX_CONTEXT）
- `.gitignore` — 修正 .env.example 可提交

### Bug 修复与优化

1. **TS 编译错误修复（5 个）**：未使用变量、类型断言、Segment 字段补全
2. **音频文件名作标题**：prompt 模板增加 `{audio_title}` 占位符，全链路传递
3. **预览改为新页面**：移除弹窗内联预览，新增 `/api/tasks/{id}/summarize/preview` 端点（marked.js 渲染 HTML）
4. **按钮布局优化**：预览按钮全宽在上，下载+重新总结并排在下

### README 更新

- 新增 AI 总结功能说明
- 新增环境变量配置表
- 新增 AI 总结 API 端点文档
- 新增预设说明表
- 更新项目结构（summarize.py + prompts/ + .env）
- 新增 FAQ：.env 修改需重启、Prompt 热更新

## 技术要点

- **异步任务模式**：POST 创建任务 → 后台线程处理 → 前端 2s 轮询状态 → 完成后操作
- **分段处理**：超长转录按 token 估算自动分段，预留 30% 空间给输出
- **Prompt 热更新**：每次调用从文件系统读取模板，修改 `.md` 文件即生效
- **.env 冷加载**：环境变量仅在启动时读取，修改后必须重启服务

## 提交记录

| SHA | 说明 |
|-----|------|
| `e26eaca` | 按钮布局优化 |
| `0eec2b9` | 音频文件名作标题 + 预览新页面 |
| `3d9bd9d` | AI 总结功能主体 |

## 待办

- [ ] 填写 `.env` 中的 `WINASR_LLM_API_KEY` 并测试完整流程
- [ ] 长音频多段处理的实际验证
- [ ] 轮询超时机制（当前无超时限制）

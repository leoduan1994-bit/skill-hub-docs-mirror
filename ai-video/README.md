# 🎬 AI 视频生成工具

输入**提示词**或上传一张**图片**，一键生成视频。

- **文生视频**：用一段文字描述生成视频
- **图生视频**：上传一张图片，让它「动起来」
- 后端为 **Replicate** 的安全代理（API key 只留在服务端，不进浏览器）
- 未配置 API key 时自动进入**演示模式**：浏览器本地用 Canvas + MediaRecorder 合成占位视频，完整体验全流程，无需联网

> 单文件前端（`public/index.html`）+ 零依赖 Node 后端（`server.js`），无需 `npm install`。

---

## 快速开始

需要 **Node.js 18+**（自带 `fetch`）。

```bash
cd ai-video

# 1) 配置（可选：不配也能跑演示模式）
cp .env.example .env
# 编辑 .env，填入 REPLICATE_API_TOKEN

# 2) 启动
node server.js
# 或： npm start
```

打开 <http://localhost:3000> 即可使用。

- 控制台显示「**演示模式**」→ 未配置 token，视频在浏览器本地合成。
- 控制台显示「**已连接 Replicate**」→ 真实 AI 生成。

> 也可以直接用浏览器打开 `public/index.html`（不启动后端），会以纯前端演示模式运行。

---

## 获取 Replicate Token

1. 注册 / 登录 <https://replicate.com>
2. 到 <https://replicate.com/account/api-tokens> 创建 token（形如 `r8_xxx`）
3. 填进 `.env` 的 `REPLICATE_API_TOKEN`，重启服务

> Replicate 按调用计费，视频模型通常每条几美分到几十美分不等，请留意账单。

---

## 工作原理

```
浏览器 ──POST /api/generate──▶ Node 后端 ──▶ Replicate 创建任务
        ◀──── { id } ────────
浏览器 ──GET  /api/status/:id─▶ Node 后端 ──▶ Replicate 查询任务
        ◀── { status, output } ──   （每 2.5s 轮询直到完成）
```

视频生成较慢（通常 1–5 分钟），所以采用「创建任务 + 轮询」的方式，前端实时显示进度。
上传的图片会在浏览器端压缩到 ≤1024px 再以 data URI 传给模型。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config` | 返回是否演示模式、可用模型列表 |
| `POST` | `/api/generate` | 创建生成任务，返回 `{ id }` |
| `GET` | `/api/status/:id` | 查询任务状态与结果 |

---

## 内置模型

在 `server.js` 的 `MODELS` 中配置：

| 模型 | 文生 | 图生 | 说明 |
| --- | :-: | :-: | --- |
| `minimax/video-01`（默认） | ✅ | ✅ | MiniMax Hailuo，画质高 |
| `kwaivgi/kling-v1.6-standard` | ✅ | ✅ | 可控时长/画幅 |
| `bytedance/seedance-1-lite` | ✅ | ✅ | 速度快、性价比高 |

### 更换 / 新增模型

到 <https://replicate.com/collections/text-to-video> 或
<https://replicate.com/collections/image-to-video> 挑选模型，把它加进 `MODELS`：

```js
'owner/model-name': {
  label: '显示名称',
  desc: '一句话描述',
  supportsImage: true,            // 是否支持图生视频
  controls: ['duration', 'aspectRatio', 'negativePrompt'], // 要显示的控件
  // 把统一参数映射成该模型 input 字段（字段名以模型页面的 API 文档为准）
  buildInput: ({ prompt, image, duration, aspectRatio, negativePrompt }) => ({
    prompt,
    ...(image ? { start_image: image } : {}),
  }),
  // 若该模型需要固定版本号，加： version: 'xxxxxxxx'
}
```

> 不同模型的 input 字段名不一样（如首帧图可能叫 `first_frame_image` / `start_image` / `image`），
> 以对应模型页面右侧的「API」文档为准。

---

## 文件结构

```
ai-video/
├── server.js          # 零依赖 Node 后端（静态服务 + Replicate 代理）
├── public/
│   └── index.html     # 单文件前端 UI（含演示模式本地合成）
├── package.json
├── .env.example       # 配置模板
└── README.md
```

---

## 常见问题

- **一直停在「正在生成」**：视频模型本身较慢，正常 1–5 分钟；超过 6 分钟会提示超时。
- **报错 402 / 余额不足**：Replicate 账户需要绑定支付方式或充值。
- **报错找不到模型**：该模型 slug 可能已更新，去 Replicate 确认后修改 `MODELS`。
- **演示模式下载是 `.webm`**：本地合成产物为 WebM；真实生成通常是 `.mp4`。
- **Safari 无法本地合成**：演示模式依赖 `MediaRecorder`，建议用 Chrome / Edge；真实模式不受影响。

## 提示词小技巧

一个好的提示词通常包含：**主体 + 动作 + 场景 + 镜头 + 风格**。
例如：「*一只柯基在海边奔跑，金色夕阳，海浪拍岸，电影感运镜，4K*」。

#!/usr/bin/env node
'use strict';

/**
 * AI 视频生成工具 · 零依赖后端
 * --------------------------------------------------
 * - 把前端静态页面 (public/) 提供出去
 * - 作为 Replicate API 的代理，保证 API key 不进入浏览器
 * - 提供 创建任务 / 轮询状态 两个接口，前端负责展示进度
 *
 * 运行：  node server.js   （需要 Node 18+，自带 fetch）
 * 配置：  在 .env 里设置 REPLICATE_API_TOKEN
 */

const http = require('http');
const fs = require('fs');
const path = require('path');

// ---------- 极简 .env 解析（无第三方依赖） ----------
(function loadEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  for (const rawLine of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = val;
  }
})();

const PORT = parseInt(process.env.PORT || '3000', 10);
const TOKEN = (process.env.REPLICATE_API_TOKEN || '').trim();
const DEMO = !TOKEN; // 未配置 token → 演示模式（前端本地生成）
const REPLICATE_API = 'https://api.replicate.com/v1';
const MAX_BODY = 30 * 1024 * 1024; // 30MB，足够容纳压缩后的图片 base64
const PUBLIC_DIR = path.join(__dirname, 'public');

// ---------- 模型预设 ----------
// key 为 Replicate 上的 "owner/name"。
// buildInput：把统一的参数映射成具体模型的 input 字段。
// controls：前端要额外渲染哪些控件。
// 想换模型：到 https://replicate.com/collections/text-to-video 找到模型，
// 把它的 input 字段按下面的方式映射进来即可。
const MODELS = {
  'minimax/video-01': {
    label: 'MiniMax Hailuo · video-01',
    desc: '通用文/图生视频，画质高（约 6 秒）',
    supportsImage: true,
    controls: [],
    buildInput: ({ prompt, image }) => ({
      prompt: prompt || '',
      prompt_optimizer: true,
      ...(image ? { first_frame_image: image } : {}),
    }),
  },
  'kwaivgi/kling-v1.6-standard': {
    label: 'Kling v1.6 Standard',
    desc: '可控时长/画幅，支持首帧图',
    supportsImage: true,
    controls: ['duration', 'aspectRatio', 'negativePrompt'],
    buildInput: ({ prompt, image, duration, aspectRatio, negativePrompt }) => ({
      prompt: prompt || '',
      cfg_scale: 0.5,
      duration: Number(duration) === 10 ? 10 : 5,
      aspect_ratio: ['16:9', '9:16', '1:1'].includes(aspectRatio) ? aspectRatio : '16:9',
      ...(negativePrompt ? { negative_prompt: negativePrompt } : {}),
      ...(image ? { start_image: image } : {}),
    }),
  },
  'bytedance/seedance-1-lite': {
    label: 'ByteDance Seedance 1 Lite',
    desc: '速度快、性价比高，支持首帧图',
    supportsImage: true,
    controls: ['duration', 'aspectRatio'],
    buildInput: ({ prompt, image, duration, aspectRatio }) => ({
      prompt: prompt || '',
      duration: Number(duration) === 10 ? 10 : 5,
      aspect_ratio: ['16:9', '9:16', '1:1', '4:3', '3:4'].includes(aspectRatio) ? aspectRatio : '16:9',
      resolution: '720p',
      ...(image ? { image } : {}),
    }),
  },
};
const DEFAULT_MODEL = process.env.DEFAULT_MODEL && MODELS[process.env.DEFAULT_MODEL]
  ? process.env.DEFAULT_MODEL
  : 'minimax/video-01';

// ---------- Replicate 调用辅助 ----------
async function replicate(pathPart, options = {}) {
  const res = await fetch(REPLICATE_API + pathPart, {
    ...options,
    headers: {
      Authorization: 'Bearer ' + TOKEN,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let json;
  try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text }; }
  return { ok: res.ok, status: res.status, json };
}

function createPrediction(modelId, input) {
  const preset = MODELS[modelId];
  if (preset && preset.version) {
    return replicate('/predictions', {
      method: 'POST',
      body: JSON.stringify({ version: preset.version, input }),
    });
  }
  const [owner, name] = modelId.split('/');
  return replicate(`/models/${owner}/${name}/predictions`, {
    method: 'POST',
    body: JSON.stringify({ input }),
  });
}

// 视频模型的 output 可能是 字符串 / 字符串数组 / 对象，统一取出可播放的 URL
function extractVideoUrl(output) {
  if (!output) return null;
  if (typeof output === 'string') return output;
  if (Array.isArray(output)) {
    const found = output.find((x) => typeof x === 'string');
    return found || null;
  }
  if (typeof output === 'object') {
    return output.video || output.url || output.output || null;
  }
  return null;
}

// ---------- HTTP 辅助 ----------
function sendJSON(res, status, obj) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}

function readJSONBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error('请求体过大，请使用更小的图片'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      try {
        const text = Buffer.concat(chunks).toString('utf8');
        resolve(text ? JSON.parse(text) : {});
      } catch {
        reject(new Error('请求体不是合法的 JSON'));
      }
    });
    req.on('error', reject);
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
};

function serveStatic(res, pathname) {
  let rel = pathname === '/' ? '/index.html' : pathname;
  rel = decodeURIComponent(rel);
  const filePath = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!filePath.startsWith(PUBLIC_DIR)) {
    res.writeHead(403); res.end('Forbidden'); return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not Found');
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}

// ---------- 路由 ----------
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const pathname = url.pathname;

  // 前端启动时拉取：是否演示模式 + 可用模型
  if (pathname === '/api/config' && req.method === 'GET') {
    return sendJSON(res, 200, {
      demo: DEMO,
      defaultModel: DEFAULT_MODEL,
      models: Object.entries(MODELS).map(([id, m]) => ({
        id,
        label: m.label,
        desc: m.desc,
        supportsImage: m.supportsImage,
        controls: m.controls || [],
      })),
    });
  }

  // 创建生成任务
  if (pathname === '/api/generate' && req.method === 'POST') {
    let body;
    try { body = await readJSONBody(req); }
    catch (e) { return sendJSON(res, 400, { error: e.message }); }

    const { model, prompt, image, duration, aspectRatio, negativePrompt } = body || {};
    const modelId = model && MODELS[model] ? model : DEFAULT_MODEL;
    const preset = MODELS[modelId];

    if (!prompt && !image) {
      return sendJSON(res, 400, { error: '请至少提供提示词或一张图片' });
    }
    if (image && !preset.supportsImage) {
      return sendJSON(res, 400, { error: '该模型不支持图片输入，请更换模型' });
    }
    if (DEMO) {
      return sendJSON(res, 400, {
        error: '当前为演示模式（未配置 REPLICATE_API_TOKEN）。真实生成请在 .env 设置 token 后重启服务。',
      });
    }

    const input = preset.buildInput({ prompt, image, duration, aspectRatio, negativePrompt });
    try {
      const { ok, status, json } = await createPrediction(modelId, input);
      if (!ok) {
        const msg = (json && (json.detail || json.title)) || `Replicate 创建任务失败 (${status})`;
        return sendJSON(res, status || 502, { error: msg, detail: json });
      }
      return sendJSON(res, 200, { id: json.id, status: json.status });
    } catch (e) {
      return sendJSON(res, 502, { error: '无法连接 Replicate：' + e.message });
    }
  }

  // 轮询任务状态
  if (pathname.startsWith('/api/status/') && req.method === 'GET') {
    if (DEMO) return sendJSON(res, 400, { error: '演示模式下没有后端任务' });
    const id = decodeURIComponent(pathname.slice('/api/status/'.length));
    if (!id) return sendJSON(res, 400, { error: '缺少任务 ID' });
    try {
      const { ok, status, json } = await replicate('/predictions/' + encodeURIComponent(id));
      if (!ok) {
        return sendJSON(res, status || 502, { error: (json && json.detail) || '查询失败', detail: json });
      }
      return sendJSON(res, 200, {
        id: json.id,
        status: json.status, // starting | processing | succeeded | failed | canceled
        output: extractVideoUrl(json.output),
        error: json.error || null,
        logs: typeof json.logs === 'string' ? json.logs.trim().split('\n').slice(-3).join('\n') : null,
      });
    } catch (e) {
      return sendJSON(res, 502, { error: '查询失败：' + e.message });
    }
  }

  // 其余走静态文件
  return serveStatic(res, pathname);
});

server.listen(PORT, () => {
  const line = '─'.repeat(46);
  console.log('\n' + line);
  console.log('  🎬  AI 视频生成工具已启动');
  console.log('  ➜  http://localhost:' + PORT);
  if (DEMO) {
    console.log('  模式：演示模式（前端本地生成占位视频）');
    console.log('  提示：在 .env 设置 REPLICATE_API_TOKEN 后重启，即可真实生成');
  } else {
    console.log('  模式：已连接 Replicate（真实生成）');
    console.log('  默认模型：' + DEFAULT_MODEL);
  }
  console.log(line + '\n');
});

module.exports = {
  apps: [
    {
      name: "winasr-backend",           // 后端服务名称
      cwd: "/Users/garphy/WinASR",  // ⚠️ 重要：改为你的绝对路径
      script: "run_server.py",          // 启动脚本
      interpreter: "/Users/garphy/WinASR/.venv/bin/python", // ⚠️ 虚拟环境里的Python解释器
      args: "--debug",                  // 传递给Python脚本的参数
      exec_mode: "fork",                // Python应用使用fork模式
      watch: false,                     // 生产环境建议设为 false
      //max_memory_restart: "500M",       // 内存超过500M自动重启
      max_memory_restart: "4G",         // RSS 超 4G 自动重启（兜底防泄漏）
      error_file: "./logs/backend-error.log",  // 错误日志
      out_file: "./logs/backend-out.log",      // 输出日志
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: true,
      autorestart: true,                // 自动重启
      env: {
        NODE_ENV: "production",         // 可添加其他环境变量
        PYTORCH_MPS_HIGH_WATERMARK_RATIO: "0.0",  // MPS GPU 用完立即归还 OS
      },
    },
    {
      name: "winasr-frontend",          // 前端服务名称
      cwd: "/Users/garphy/WinASR/app",  // ⚠️ 同样改为你的绝对路径
      script: "pnpm",                   // 使用 pnpm 命令
      args: "run dev",                  // 开发模式，生产环境建议用 "run start"
      interpreter: "none",              // 关键：避免 PM2 误用 node 解释器
      exec_mode: "fork",
      watch: false,
      max_memory_restart: "1G",
      error_file: "./logs/frontend-error.log",
      out_file: "./logs/frontend-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: true,
      autorestart: true,
      env: {
        NODE_ENV: "development",        // 前端开发环境
      },
    },
  ],
};
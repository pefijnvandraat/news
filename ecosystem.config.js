// pm2 process definition.
//
//   pm2 start ecosystem.config.js
//
// Windows note: the interpreter is pythonw.exe rather than python.exe so the
// service runs without a console window. pm2 still captures stdout/stderr
// through its pipes, and run.py additionally writes data/nieuws.log.
const path = require("path");

const PYTHONW =
  process.env.NIEUWS_PYTHONW ||
  "C:\\Users\\paulfijn\\AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe";

module.exports = {
  apps: [
    {
      name: "nieuws",
      script: PYTHONW,
      // Absolute path so the process is self-identifying on the command line
      // (Task Manager, and the Exam Mode stray-daemon sweep both match on it).
      args: [path.join(__dirname, "run.py")],
      interpreter: "none",
      cwd: __dirname,
      env: {
        PYTHONUTF8: "1",
        PYTHONIOENCODING: "utf-8",
        NIEUWS_PORT: "8500",
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: 10000,
      out_file: path.join(__dirname, "data", "pm2-out.log"),
      error_file: path.join(__dirname, "data", "pm2-error.log"),
      merge_logs: true,
    },
  ],
};

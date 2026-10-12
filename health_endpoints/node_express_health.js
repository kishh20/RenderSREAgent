/**
 * Drop-in /health route for Node.js / Express apps on Render.
 *
 * Usage:
 *   app.use(require('./node_express_health'));
 */
const express = require('express');
const router = express.Router();

router.get('/health', (req, res) => {
  const memMb = Math.round((process.memoryUsage().rss / 1024 / 1024) * 100) / 100;
  res.status(200).json({
    status: 'ok',
    uptime_seconds: Math.floor(process.uptime()),
    timestamp: new Date().toISOString(),
    version: (process.env.RENDER_GIT_COMMIT || '1.0.0').slice(0, 7),
    memory_mb: memMb,
    checks: {
      runtime: 'ready',
      memory_headroom: memMb < 460 ? 'ok' : 'warning_near_512mb_limit',
    },
  });
});

module.exports = router;

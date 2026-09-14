module.exports = {
  apps: [{
    name: "purity-beans",
    script: "node",
    args: "node_modules/next/dist/bin/next start -p 3001 -H 0.0.0.0",
    cwd: __dirname,
    restart_delay: 3000,
    max_restarts: 20,
    watch: false,
    env: { NODE_ENV: "production" }
  }]
};

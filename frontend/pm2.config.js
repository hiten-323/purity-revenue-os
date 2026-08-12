module.exports = {
  apps: [{
    name: "purity-beans",
    script: "node",
    args: "node_modules/next/dist/bin/next start -p 3001 -H 0.0.0.0",
    cwd: "C:\\Users\\hiten\\Desktop\\ppp\\claude\\CODE\\purity_beans_ai\\jules_session\\frontend",
    restart_delay: 3000,
    max_restarts: 20,
    watch: false,
    env: { NODE_ENV: "production" }
  }]
};

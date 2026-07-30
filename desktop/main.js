/**
 * AuditWorkbench Desktop — Electron 桌面壳
 * 加载 OpenSquilla 网关上的审计实务工坊页面
 */
const { app, BrowserWindow, Menu, dialog, shell } = require('electron');
const path = require('path');

// 配置
const CONFIG = {
  gatewayUrl: process.env.AW_GATEWAY_URL || 'http://192.168.3.164:18791',
  token: process.env.AW_TOKEN || 'admin',
  homePage: '/control/static/audit/index.html',
};

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: '审计实务工坊',
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    // 隐藏默认菜单栏 (Windows)
    autoHideMenuBar: true,
  });

  // 构建完整 URL
  const url = `${CONFIG.gatewayUrl}${CONFIG.homePage}?token=${CONFIG.token}`;
  console.log('Loading:', url);
  mainWindow.loadURL(url);

  // 设置窗口标题
  mainWindow.on('page-title-updated', (e) => e.preventDefault());
  mainWindow.on('ready-to-show', () => {
    mainWindow.setTitle('审计实务工坊 — AI多智能体审计分析平台');
  });

  // 外部链接用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  // 自定义菜单
  const menu = Menu.buildFromTemplate([
    {
      label: '文件',
      submenu: [
        { label: '返回首页', click: () => mainWindow.loadURL(`${CONFIG.gatewayUrl}${CONFIG.homePage}?token=${CONFIG.token}`) },
        { type: 'separator' },
        { label: '退出', click: () => app.quit() },
      ],
    },
    {
      label: '视图',
      submenu: [
        { label: '刷新', accelerator: 'F5', click: () => mainWindow.reload() },
        { label: '全屏', accelerator: 'F11', click: () => mainWindow.setFullScreen(!mainWindow.isFullScreen()) },
        { type: 'separator' },
        { label: '开发者工具', accelerator: 'F12', click: () => mainWindow.webContents.toggleDevTools() },
      ],
    },
    {
      label: '帮助',
      submenu: [
        { label: '关于审计实务工坊', click: () => dialog.showMessageBox(mainWindow, { type: 'info', title: '关于', message: '审计实务工坊 v1.0.0', detail: 'AI多智能体审计分析平台\n基于 OpenSquilla 0.5.0rc4\n\n面向国家审计机关' }) },
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

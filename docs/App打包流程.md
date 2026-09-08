# OpsAgent Android App 打包全流程（Capacitor 远程 URL 模式）

> 模式：App 只是一个壳，打开后直接加载服务器网页 `http://118.31.106.227`
> 优势：服务器代码（前端/后端）任何更新，App 下次打开自动生效，**永远不用重新打包**
> 最后更新：2026-09-08

---

## 目录
1. [前置环境准备](#1-前置环境准备)
2. [安装 Capacitor](#2-安装-capacitor)
3. [配置远程 URL 模式](#3-配置远程-url-模式)
4. [Android 允许 HTTP 明文](#4-android-允许-http-明文)
5. [生成 Android 工程](#5-生成-android-工程)
6. [打 Debug APK](#6-打-debug-apk)
7. [打 Release APK（正式签名）](#7-打-release-apk正式签名)
8. [安装到手机](#8-安装到手机)
9. [App 更新流程](#9-app-更新流程)
10. [常见问题](#10-常见问题)

---

## 1. 前置环境准备

### 1.1 JDK 17
- 下载 Adoptium Temurin 17：https://adoptium.net/temurin/releases/?version=17
- 安装后配置环境变量 `JAVA_HOME` 指向 JDK 目录
- 验证（PowerShell）：

```powershell
java -version
# 应显示 17.x.x
```

### 1.2 Android Studio
- 下载：https://developer.android.com/studio
- 安装时默认勾选 Android SDK、Android SDK Platform、Android Virtual Device
- 首次打开会自动下载 SDK（选最新稳定版 API 34/35）

### 1.3 环境变量
系统环境变量加：

```
ANDROID_HOME = C:\Users\你的用户名\AppData\Local\Android\Sdk
```

Path 里加：

```
%ANDROID_HOME%\platform-tools
%ANDROID_HOME%\tools
%ANDROID_HOME%\tools\bin
```

验证：

```powershell
adb --version
```

---

## 2. 安装 Capacitor

本地 PowerShell：

```powershell
cd F:\个人项目\OpsAgent\frontend

# 安装依赖
npm install @capacitor/core @capacitor/cli @capacitor/android

# 初始化（交互式，按提示填）
npx cap init
```

交互填写：
- App name：`OpsAgent`
- App Package ID：`com.opsagent.app`
- Web directory：`dist`

---

## 3. 配置远程 URL 模式

打开 `frontend/capacitor.config.ts`（或 .json），改成：

```typescript
import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.opsagent.app',
  appName: 'OpsAgent',
  // 关键：直接加载服务器网页，不读本地 dist
  server: {
    url: 'http://118.31.106.227',
    cleartext: true
  },
  android: {
    allowMixedContent: true
  },
  ios: {
    contentInset: 'automatic'
  }
};

export default config;
```

> 域名/HTTPS 配好后，把 `server.url` 改成 `https://anc0n.xyz`，重新打一次 APK 即可。

---

## 4. Android 允许 HTTP 明文

Android 9+ 默认禁止 HTTP，我们的服务器还是 HTTP，需要放开。

### 4.1 创建 network_security_config.xml

文件路径：`frontend/android/app/src/main/res/xml/network_security_config.xml`（先 add android 后再建）：

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="true">118.31.106.227</domain>
    <domain includeSubdomains="true">anc0n.xyz</domain>
  </domain-config>
</network-security-config>
```

### 4.2 AndroidManifest 引用

编辑 `frontend/android/app/src/main/AndroidManifest.xml`，在 `<application>` 标签里加：

```xml
<application
    android:networkSecurityConfig="@xml/network_security_config"
    android:usesCleartextTraffic="true"
    ...existing attributes...>
```

---

## 5. 生成 Android 工程

```powershell
cd F:\个人项目\OpsAgent\frontend

# 不需要 npm run build（远程模式不用本地 dist）
# 直接添加 android 平台
npx cap add android

# 同步配置
npx cap sync android
```

---

## 6. 打 Debug APK

### 方式 A：Android Studio 图形界面（推荐新手）

```powershell
npx cap open android
```

Android Studio 打开后：
1. 等 Gradle 同步完成（首次几分钟~十几分钟，会自动下依赖）
2. 菜单：**Build → Build App Bundle(s) / APK(s) → Build APK(s)**
3. 等待底部 Build 进度完成
4. 右上角通知 "locate" 链接点击打开 APK 目录

APK 位置：

```
frontend\android\app\build\outputs\apk\debug\app-debug.apk
```

### 方式 B：命令行打 debug APK

```powershell
cd F:\个人项目\OpsAgent\frontend\android
.\gradlew assembleDebug
```

APK 同样在 `app\build\outputs\apk\debug\app-debug.apk`。

---

## 7. 打 Release APK（正式签名）

Debug APK 右上角会显示"Debug"字样，且任何人可安装。正式分发用 Release 签名包。

### 7.1 生成签名 keystore

```powershell
keytool -genkey -v -keystore opsagent-release.keystore -alias opsagent -keyalg RSA -keysize 2048 -validity 36500
```

按提示输入密码、姓名等。生成的 `opsagent-release.keystore` **妥善保存**（丢了以后 App 无法升级覆盖）。

### 7.2 app/build.gradle 配置签名

编辑 `frontend/android/app/build.gradle`，在 `android { ... }` 里加：

```gradle
android {
    ...
    signingConfigs {
        release {
            storeFile file('../../../opsagent-release.keystore')
            storePassword 你设的keystore密码
            keyAlias 'opsagent'
            keyPassword 你设的key密码
        }
    }
    buildTypes {
        release {
            signingConfig signingConfigs.release
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
    }
}
```

### 7.3 打 release APK

```powershell
cd F:\个人项目\OpsAgent\frontend\android
.\gradlew assembleRelease
```

APK 位置：

```
frontend\android\app\build\outputs\apk\release\app-release.apk
```

---

## 8. 安装到手机

**方法1：微信/钉钉传 APK**
- 把 APK 发到群里，同事手机点开安装
- 首次安装需在手机设置允许"安装未知来源应用"

**方法2：数据线 adb 安装**（调试用）

手机开启"开发者选项 → USB 调试"，连接电脑：

```powershell
adb install -r app-debug.apk
```

---

## 9. App 更新流程（远程模式的核心优势）

**服务器代码更新（前端/后端/数据库）：**
- ✅ **不需要重新打 APK**
- 同事下次打开 App 自动加载最新网页
- 最多在 App 启动时下拉刷新即可

**需要重新打 APK 的场景（很少）：**
- 改 App 图标
- 改启动页
- 改 App 名称
- 改 `server.url`（如域名/HTTPS 切换）
- 改权限（如加相机/相册）

---

## 10. 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| App 打开白屏 | HTTP 没开明文 | 检查 network_security_config.xml 和 AndroidManifest |
| 打开一直转圈 | 服务器没起/网络不通 | 手机浏览器先访问 http://118.31.106.227 验证 |
| Gradle 同步慢/失败 | 国内访问 Google 慢 | build.gradle 里替换仓库为阿里云镜像 |
| 安装失败"解析包错误" | 签名问题 | 卸载旧版本再装；debug 包不能覆盖 release 包 |
| 手机显示"不安全连接" | 还是 HTTP | 等域名备案后上 HTTPS |
| iOS 想打包 | 必须 Mac + Apple 开发者账号 $99/年 | 个人项目只做 Android |

---

## 附：一键打包命令（Debug）

```powershell
cd F:\个人项目\OpsAgent\frontend
npx cap sync android
cd android
.\gradlew assembleDebug
# APK 在 app\build\outputs\apk\debug\app-debug.apk
```

---

*远程 URL 模式 = 一次打包，永久使用；服务器怎么改，App 跟着变。*

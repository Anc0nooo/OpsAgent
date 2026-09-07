/**
 * 头像文件处理：压缩为 256x256 JPEG data URL（cover 裁剪）
 * - 服务端限制 2MB；前端统一压缩后通常 < 30KB，节省 MySQL 存储与带宽
 * - JPEG 不支持透明，先铺白底
 */

const AVATAR_SIZE = 256

export async function fileToAvatarDataUrl(file: File): Promise<string> {
  // 1. 文件 → data URL
  const rawDataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })

  // 2. data URL → Image
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const el = new Image()
    el.onload = () => resolve(el)
    el.onerror = () => reject(new Error('图片解析失败'))
    el.src = rawDataUrl
  })

  // 3. cover 裁剪绘制到 256x256 canvas
  const canvas = document.createElement('canvas')
  canvas.width = AVATAR_SIZE
  canvas.height = AVATAR_SIZE
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, AVATAR_SIZE, AVATAR_SIZE)

  const scale = Math.max(AVATAR_SIZE / img.width, AVATAR_SIZE / img.height)
  const w = img.width * scale
  const h = img.height * scale
  ctx.drawImage(img, (AVATAR_SIZE - w) / 2, (AVATAR_SIZE - h) / 2, w, h)

  // 4. 导出 JPEG data URL（质量 0.85）
  return canvas.toDataURL('image/jpeg', 0.85)
}

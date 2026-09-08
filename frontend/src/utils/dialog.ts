import { ElMessageBox } from 'element-plus'
import { showConfirmDialog } from 'vant'

/**
 * 危险操作确认弹窗（删除/清空等）
 * - 桌面端：Element Plus MessageBox，居中、圆角，确定按钮 danger 样式
 * - 移动端（<768px）：Vant Dialog，居中显示、触控友好、按钮热区足够大
 * - 返回 Promise<boolean>：true=确认 / false=取消，便于 `if (!(await ...)) return` 用法
 */
export function confirmDanger(message: string, title = '请确认'): Promise<boolean> {
  // 移动端：Vant 确认框（confirm promise resolve=确认 / reject=取消）
  if (window.innerWidth < 768) {
    return showConfirmDialog({
      title,
      message,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      confirmButtonColor: '#ee0a24',
    })
      .then(() => true)
      .catch(() => false)
  }

  return ElMessageBox.confirm(message, title, {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
    confirmButtonClass: 'el-button--danger',
    cancelButtonClass: 'el-button--default',
    center: true,
  })
    .then(() => true)
    .catch(() => false)
}

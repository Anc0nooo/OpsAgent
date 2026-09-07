import { ElMessageBox } from 'element-plus'

/**
 * 危险操作确认弹窗（删除/清空等）
 * - 居中显示、遮罩半透明、圆角，与 Element Plus 整体风格统一
 * - 确定按钮 danger 样式
 * - 返回 Promise<boolean>：true=确认 / false=取消，便于 `if (!(await ...)) return` 用法
 */
export function confirmDanger(message: string, title = '请确认'): Promise<boolean> {
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

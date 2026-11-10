// One presentation contract for Today, the task list and event details.
export function taskProgressView(task, commands = []) {
  const local = commands.filter(item => item.resourceKey === 'task_progress:' + task.id)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  const awaitingRead = (local?.server?._cloud?.revision || 0) > (task.progress?._cloud?.revision || 0);
  const source = awaitingRead ? local.server : task.progress || {};
  const localPending = local?.state === 'local_pending';
  const localConflict = local?.state === 'needs_confirmation' || local?.state === 'failed';
  // A refreshed server progress revision supersedes an old failed local intent.
  const conflict = source.application_state === 'needs_confirmation' ||
    (localConflict && (source._cloud?.revision || 0) <= (local.body.expected_revision || 0));
  const pending = localPending || (!conflict && source.application_state === 'pending');
  const desired = localPending ? local.body.value.completed : source.completed;
  const message = localPending ? (local.error ? '同步失败，已保存在此设备，等待重试。' : '已保存在此设备，等待发送。')
    : conflict ? (source.message || local?.error?.message || '同步未完成，请核对最新任务后重试。')
    : source.application_state === 'pending' ? '云端已接收，等待 Windows 应用。'
    : source.application_state === 'applied' ? '已应用；当前显示最新任务状态。' : '';
  return {checked:pending && typeof desired === 'boolean' ? desired : task.status === 'done',
    pending, sending:localPending || awaitingRead, conflict, message,
    desired:conflict && localConflict ? local.body.value.completed : source.completed};
}

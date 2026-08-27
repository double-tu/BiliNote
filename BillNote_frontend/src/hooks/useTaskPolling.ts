import { useEffect, useRef } from 'react'
import { useTaskStore } from '@/store/taskStore'
import { get_task_status } from '@/services/note.ts'
import toast from 'react-hot-toast'

export const useTaskPolling = (interval = 3000) => {
  const tasks = useTaskStore(state => state.tasks)
  const updateTaskContent = useTaskStore(state => state.updateTaskContent)

  const tasksRef = useRef(tasks)
  const pollingRef = useRef(false)

  // 每次 tasks 更新，把最新的 tasks 同步进去
  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    const poll = async () => {
      if (pollingRef.current) return

      // failureVerified 缺失的是旧版前端因断网误标的 FAILED，需要向后端重新核验。
      const pendingTasks = tasksRef.current.filter(task =>
        task.status !== 'SUCCESS'
        && !(task.status === 'FAILED' && task.failureVerified === true)
      )

      // 无活跃任务时跳过轮询
      if (pendingTasks.length === 0) return

      pollingRef.current = true
      try {
        for (const task of pendingTasks) {
          try {
            const res = await get_task_status(task.id)
            const { status } = res

            if (status === 'SUCCESS') {
              const { markdown, transcript, audio_meta } = res.result
              if (task.status !== 'SUCCESS') toast.success('笔记生成成功')
              updateTaskContent(task.id, {
                status,
                failureVerified: false,
                markdown,
                transcript,
                audioMeta: audio_meta,
              })
            } else if (status === 'FAILED') {
              updateTaskContent(task.id, { status, failureVerified: true })
              console.warn(`⚠️ 任务 ${task.id} 失败`)
            } else if (status && (status !== task.status || task.failureVerified)) {
              updateTaskContent(task.id, { status, failureVerified: false })
            }
          } catch (e) {
            // 网络中断、后端重启和超时都不是任务失败。保留当前状态，下轮继续核验。
            console.error(`❌ 任务 ${task.id} 状态轮询暂时不可用：`, e)
          }
        }
      } finally {
        pollingRef.current = false
      }
    }

    void poll()
    const timer = setInterval(poll, interval)

    return () => clearInterval(timer)
  }, [interval, updateTaskContent])
}

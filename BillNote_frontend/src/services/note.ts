import request from '@/utils/request'
import toast from 'react-hot-toast'

export const generateNote = async (data: {
  video_url: string
  platform: string
  quality: string
  model_name: string
  provider_id: string
  task_id?: string
  format: Array<string>
  style: string
  extras?: string
  video_understand?: boolean
  video_understanding?: boolean
  force_transcription?: boolean
  visual_model_name?: string
  visual_provider_id?: string
  video_interval?: number
  grid_size: Array<number>
}, options?: { silent?: boolean }) => {
  try {
    console.log('generateNote', data)
    const response = await request.post('/generate_note', data)

    if (!response) {
      throw new Error('后端未返回任务信息')
    }
    if (!options?.silent) toast.success('笔记生成任务已提交！')

    console.log('res', response)
    // 成功提示

    return response
  } catch (e: any) {
    console.error('❌ 请求出错', e)

    // 错误提示
    // toast.error('笔记生成失败，请稍后重试')

    throw e // 抛出错误以便调用方处理
  }
}

export const delete_task = async ({ video_id, platform }) => {
  try {
    const data = {
      video_id,
      platform,
    }
    const res = await request.post('/delete_task', data)


      toast.success('任务已成功删除')
      return res
  } catch (e) {
    toast.error('请求异常，删除任务失败')
    console.error('❌ 删除任务失败:', e)
    throw e
  }
}

export const get_task_status = async (task_id: string) => {
  try {
    // 轮询属于后台探测：断网时由轮询器保留任务状态并稍后重试，避免重复 toast。
    return await request.get('/task_status/' + task_id, { suppressToast: true })
  } catch (e) {
    console.error('❌ 请求出错', e)
    throw e // 抛出错误以便调用方处理
  }
}

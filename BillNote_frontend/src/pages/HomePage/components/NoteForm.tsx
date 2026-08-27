/* NoteForm.tsx ---------------------------------------------------- */
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form.tsx'
import { useEffect,useState } from 'react'
import { useForm, useWatch, type FieldErrors } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import { Info, Loader2, Plus } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert.tsx'
import { generateNote } from '@/services/note.ts'
import { uploadFile } from '@/services/upload.ts'
import { useTaskStore } from '@/store/taskStore'
import { useModelStore } from '@/store/modelStore'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'
import { Checkbox } from '@/components/ui/checkbox.tsx'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { Button } from '@/components/ui/button.tsx'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select.tsx'
import { Input } from '@/components/ui/input.tsx'
import { Textarea } from '@/components/ui/textarea.tsx'
import { noteStyles, noteFormats, videoPlatforms } from '@/constant/note.ts'
import { fetchModels } from '@/services/model.ts'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'

/* -------------------- 校验 Schema -------------------- */
/** 用户粘贴的链接常缺协议头（如 bilibili.com/...），无任何 scheme 时自动补 https:// */
const withScheme = (url: string) => (/^[a-z][a-z0-9+.-]*:\/\//i.test(url) ? url : `https://${url}`)

/** 小红书常复制为整段分享文案；提交时只保留其中的链接和 xsec_token。 */
const normalizeRemoteVideoInput = (value: string, platform: string) => {
  if (platform === 'xiaohongshu') {
    const matchedUrl = value.match(/https?:\/\/[^\s]+/i)?.[0]
    return (matchedUrl || value.trim()).replace(/[，。；;、)\]}>"'”’]+$/, '')
  }
  return withScheme(value).replace(/[，。；;、),\]}>"'”’]+$/, '')
}

const createEmptyFormValues = (modelName = '') => ({
  platform: 'bilibili',
  video_url: '',
  quality: 'medium' as const,
  model_name: modelName,
  style: 'minimal',
  video_interval: 6,
  grid_size: [2, 2] as [number, number],
  format: [] as string[],
  screenshot: false,
  link: false,
  video_understanding: false,
  force_transcription: false,
  visual_model_name: '',
})

const LAST_CONFIG_KEY = 'bilinote-last-note-config'

const splitBatchInputs = (value: string, platform: string) => {
  const lines = value
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
  // 只要粘贴内容中包含完整 HTTP URL，就优先提取 URL。这样可以兼容
  // 小红书的多行分享文案，以及“说明文字 + 链接”的粘贴结果。
  const explicitUrls = value.match(/https?:\/\/[^\s]+/gi) || []
  const candidates = platform === 'local'
    ? lines
    : explicitUrls.length > 0
      ? explicitUrls
      : lines
  return Array.from(new Set(candidates
    .map(item => normalizeRemoteVideoInput(item, platform))
    .filter(Boolean)))
}

const formSchema = z
  .object({
    video_url: z.string().optional(),
    platform: z.string().nonempty('请选择平台'),
    quality: z.enum(['fast', 'medium', 'slow']),
    screenshot: z.boolean().optional(),
    link: z.boolean().optional(),
    model_name: z.string().nonempty('请选择模型'),
    format: z.array(z.string()).default([]),
    style: z.string().nonempty('请选择笔记生成风格'),
    extras: z.string().optional(),
    video_understanding: z.boolean().optional(),
    force_transcription: z.boolean().optional(),
    visual_model_name: z.string().optional(),
    video_interval: z.coerce.number().min(1).max(30).default(6).optional(),
    grid_size: z
      .tuple([z.coerce.number().min(1).max(10), z.coerce.number().min(1).max(10)])
      .default([2, 2])
      .optional(),
  })
  .superRefine(({ video_url, platform }, ctx) => {
    if (platform === 'local') {
      if (!video_url) {
        ctx.addIssue({ code: 'custom', message: '本地视频路径不能为空', path: ['video_url'] })
      }
    }
    else {
      if (!video_url) {
        ctx.addIssue({ code: 'custom', message: '视频链接不能为空', path: ['video_url'] })
      }
      else {
        try {
          // 批量模式下 video_url 可能包含多行链接；校验第一条即可，实际提交
          // 会在 onSubmit 中逐条拆分并分别提交。
          const firstCandidate = video_url.match(/https?:\/\/[^\s]+/i)?.[0]
            || video_url.split(/\r?\n/).find(item => item.trim())
            || video_url
          const url = new URL(normalizeRemoteVideoInput(firstCandidate, platform))
          if (!['http:', 'https:'].includes(url.protocol))
            throw new Error()
        }
        catch {
          ctx.addIssue({ code: 'custom', message: '请输入正确的视频链接', path: ['video_url'] })
        }
      }
    }
  })

export type NoteFormValues = z.infer<typeof formSchema>

/* -------------------- 可复用子组件 -------------------- */
const SectionHeader = ({ title, tip }: { title: string; tip?: string }) => (
  <div className="my-3 flex items-center justify-between">
    <h2 className="block">{title}</h2>
    {tip && (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
          </TooltipTrigger>
          <TooltipContent className="text-xs">{tip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )}
  </div>
)

const CheckboxGroup = ({
  value = [],
  onChange,
  disabledMap,
}: {
  value?: string[]
  onChange: (v: string[]) => void
  disabledMap: Record<string, boolean>
}) => (
  <div className="flex flex-wrap space-x-1.5">
    {noteFormats.map(({ label, value: v }) => (
      <label key={v} className="flex items-center space-x-2">
        <Checkbox
          checked={value.includes(v)}
          disabled={disabledMap[v]}
          onCheckedChange={checked =>
            onChange(checked ? [...value, v] : value.filter(x => x !== v))
          }
        />
        <span>{label}</span>
      </label>
    ))}
  </div>
)

/* -------------------- 主组件 -------------------- */
const NoteForm = () => {
  const navigate = useNavigate();
  const [isUploading, setIsUploading] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)
  const [batchMode, setBatchMode] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [hasSavedConfig, setHasSavedConfig] = useState(() => {
    try {
      return Boolean(localStorage.getItem(LAST_CONFIG_KEY))
    } catch {
      return false
    }
  })
  /* ---- 全局状态 ---- */
  const { addPendingTask, currentTaskId, setCurrentTask, getCurrentTask, retryTask } =
    useTaskStore()
  const { loadEnabledModels, modelList, showFeatureHint, setShowFeatureHint } = useModelStore()

  /* ---- 表单 ---- */
  const form = useForm<NoteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: createEmptyFormValues(modelList[0]?.model_name || ''),
  })
  const currentTask = getCurrentTask()

  /* ---- 派生状态（只 watch 一次，提高性能） ---- */
  const platform = useWatch({ control: form.control, name: 'platform' }) as string
  const videoUnderstandingEnabled = useWatch({ control: form.control, name: 'video_understanding' })
  const editing = currentTask && currentTask.id

  // 截图选项依赖视频理解。关闭视频理解后，必须同步移除旧的 screenshot
  // format，避免复选框虽然禁用但提交时仍携带历史值。
  useEffect(() => {
    if (videoUnderstandingEnabled) return
    const formats = form.getValues('format') || []
    if (formats.includes('screenshot')) {
      form.setValue(
        'format',
        formats.filter(value => value !== 'screenshot'),
        { shouldDirty: true },
      )
    }
    // 同步清掉依赖视频理解的字段，避免切换任务或重新打开表单时
    // 残留的截图/视觉模型状态被误显示或提交。
    if (form.getValues('screenshot')) {
      form.setValue('screenshot', false, { shouldDirty: true })
    }
    if (form.getValues('visual_model_name')) {
      form.setValue('visual_model_name', '', { shouldDirty: true })
    }
  }, [videoUnderstandingEnabled, form])

  const goModelAdd = () => {
    navigate("/settings/model");
  };
  /* ---- 副作用 ---- */
  useEffect(() => {
    loadEnabledModels()

    return
  }, [])
  useEffect(() => {
    if (!currentTask) return
    const { formData } = currentTask

    console.log('currentTask.formData.platform:', formData.platform)

    form.reset({
      platform: formData.platform || 'bilibili',
      video_url: formData.video_url || '',
      model_name: formData.model_name || modelList[0]?.model_name || '',
      style: formData.style || 'minimal',
      quality: formData.quality || 'medium',
      extras: formData.extras || '',
      screenshot: formData.screenshot ?? false,
      link: formData.link ?? false,
      video_understanding: formData.video_understanding ?? false,
      force_transcription: formData.force_transcription ?? false,
      visual_model_name: formData.visual_model_name ?? '',
      video_interval: formData.video_interval ?? 6,
      grid_size: formData.grid_size ?? [2, 2],
      format: formData.format ?? [],
    })
  }, [
    // 当下面任意一个变了，就重新 reset
    currentTaskId,
    // modelList 用来兜底 model_name
    modelList.length,
    // 还要加上 formData 的各字段，或者直接 currentTask
    currentTask?.formData,
  ])

  /* ---- 帮助函数 ---- */
  const isGenerating = () => !['SUCCESS', 'FAILED', undefined].includes(getCurrentTask()?.status)
  const generating = isGenerating()
  const handleFileUpload = async (file: File, cb: (url: string) => void) => {
    const formData = new FormData()
    formData.append('file', file)
    setIsUploading(true)
    setUploadSuccess(false)

    try {
  
      const  data  = await uploadFile(formData)
        cb(data.url)
        setUploadSuccess(true)
    } catch (err) {
      console.error('上传失败:', err)
      // message.error('上传失败，请重试')
    } finally {
      setIsUploading(false)
    }
  }

  const submitForm = async (values: NoteFormValues) => {
    console.log('Not even go here')
    const selectedModel = modelList.find(model => model.model_name === values.model_name)
    if (!selectedModel) {
      toast.error('当前模型不可用，请重新选择模型')
      return
    }
    const selectedVisualModel = values.visual_model_name
      ? modelList.find(model => model.model_name === values.visual_model_name)
      : undefined
    if (values.visual_model_name && !selectedVisualModel) {
      toast.error('当前视觉模型不可用，请重新选择视觉模型')
      return
    }
    const saveConfig = () => {
      try {
        const config = {
          platform: values.platform,
          quality: values.quality,
          model_name: values.model_name,
          style: values.style,
          extras: values.extras || '',
          format: values.video_understanding
            ? values.format ?? []
            : (values.format ?? []).filter(value => value !== 'screenshot'),
          screenshot: Boolean(values.video_understanding && values.screenshot),
          link: Boolean(values.link),
          video_understanding: Boolean(values.video_understanding),
          force_transcription: Boolean(values.force_transcription),
          visual_model_name: values.visual_model_name || '',
          video_interval: values.video_interval ?? 6,
          grid_size: values.grid_size ?? [2, 2],
        }
        localStorage.setItem(LAST_CONFIG_KEY, JSON.stringify(config))
        setHasSavedConfig(true)
      } catch {
        // 浏览器禁用 localStorage 时不影响任务提交。
      }
    }

    const safeFormat = values.video_understanding
      ? values.format ?? []
      : (values.format ?? []).filter(value => value !== 'screenshot')
    const normalizedValues = {
      ...values,
      format: safeFormat,
      screenshot: Boolean(values.video_understanding && values.screenshot),
    }

    if (batchMode) {
      const batchUrls = splitBatchInputs(values.video_url || '', values.platform)
      if (batchUrls.length === 0) {
        toast.error('请至少输入一条视频链接')
        return
      }
      if (values.platform === 'local' && batchUrls.length > 1) {
        toast.error('批量模式暂不支持多个本地文件路径')
        return
      }

      saveConfig()
      const batchResults = await Promise.allSettled(batchUrls.map(videoUrl =>
        generateNote({
          ...normalizedValues,
          provider_id: selectedModel.provider_id,
          visual_provider_id: selectedVisualModel?.provider_id,
          video_url: videoUrl,
          task_id: '',
        }, { silent: true }).then(response => {
          if (!response?.task_id) {
            throw new Error('后端未返回 task_id')
          }
          addPendingTask(response.task_id, values.platform, {
            ...normalizedValues,
            provider_id: selectedModel.provider_id,
            visual_provider_id: selectedVisualModel?.provider_id,
            video_url: videoUrl,
            task_id: '',
          })
          return response
        }),
      ))
      const succeeded = batchResults.filter(result => result.status === 'fulfilled').length
      const failed = batchResults.length - succeeded
      if (succeeded > 0) {
        toast.success(`已提交 ${succeeded} 个任务${failed ? `，${failed} 个提交失败` : ''}`)
      } else {
        toast.error('批量任务提交失败，请检查链接和后端状态')
      }
      return
    }

    saveConfig()
    const payload: NoteFormValues = {
      ...normalizedValues,
      video_url:
        values.platform === 'local'
          ? values.video_url
          : normalizeRemoteVideoInput(values.video_url || '', values.platform),
      provider_id: selectedModel.provider_id,
      task_id: currentTaskId || '',
      force_transcription: values.force_transcription ?? false,
      visual_model_name: values.visual_model_name || undefined,
      visual_provider_id: selectedVisualModel?.provider_id,
    }
    if (currentTaskId) {
      await retryTask(currentTaskId, payload)
      return
    }

    // message.success('已提交任务')
    try {
      const data = await generateNote(payload)
      addPendingTask(data.task_id, values.platform, payload)
    } catch (e: any) {
      // 就绪门禁：本地转写模型还没下载好。后端返回 reason='transcriber_model_not_ready'，
      // 引导用户去「设置 → 音频转写配置」下载，而不是留一个静默失败的任务。
      if (e?.data?.reason === 'transcriber_model_not_ready') {
        const downloading = e?.data?.downloading
        toast.error(
          downloading
            ? '转写模型正在下载中，请稍候再提交'
            : '转写模型尚未下载，请先去「音频转写配置」页下载',
        )
        if (!downloading) navigate('/settings/transcriber')
        return
      }
      // 其余错误：axios 拦截器已经弹过 toast，这里只兜底不让 promise 变成未处理 rejection
      console.error('提交任务失败：', e)
    }
  }
  // 防止批量请求尚未返回时重复点击，导致同一批链接被重复创建任务。
  const onSubmit = async (values: NoteFormValues) => {
    if (isSubmitting) return
    setIsSubmitting(true)
    try {
      await submitForm(values)
    } finally {
      setIsSubmitting(false)
    }
  }
  const onInvalid = (errors: FieldErrors<NoteFormValues>) => {
    console.warn('表单校验失败：', errors)
    // message.error('请完善所有必填项后再提交')
  }
  const handleCreateNew = () => {
    // 清空当前任务和所有与上一条笔记相关的表单状态，尤其是 format.screenshot。
    setCurrentTask(null)
    form.reset(createEmptyFormValues(modelList[0]?.model_name || ''))
    setBatchMode(false)
  }

  const restoreLastConfig = () => {
    try {
      const raw = localStorage.getItem(LAST_CONFIG_KEY)
      if (!raw) return
      const config = JSON.parse(raw)
      form.reset({
        ...createEmptyFormValues(modelList[0]?.model_name || ''),
        ...config,
        video_url: '',
      })
      toast.success('已还原上次配置，请填写视频链接')
    } catch {
      toast.error('还原配置失败')
    }
  }
  const FormButton = () => {
    const label = generating ? '正在生成…' : editing ? '重新生成' : batchMode ? '批量生成' : '生成笔记'

    return (
      <div className="flex gap-2">
        <Button
          type="submit"
          className={!editing ? 'w-full' : 'w-2/3' + ' bg-primary'}
          disabled={generating || isSubmitting}
        >
          {generating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {label}
        </Button>

        {editing && (
          <Button type="button" variant="outline" className="w-1/3" onClick={handleCreateNew}>
            <Plus className="mr-2 h-4 w-4" />
            新建笔记
          </Button>
        )}
      </div>
    )
  }

  /* -------------------- 渲染 -------------------- */
  return (
    <div className="h-full w-full">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit, onInvalid)} className="space-y-4">
          {/* 顶部按钮 */}
          <FormButton></FormButton>

          {/* 视频链接 & 平台 */}
          <SectionHeader title="视频链接" tip="支持 B 站、YouTube 等平台" />
          <div className="mb-2 flex justify-end gap-2">
            {!editing && (
              <Button
                type="button"
                variant={batchMode ? 'default' : 'outline'}
                size="sm"
                onClick={() => setBatchMode(value => !value)}
              >
                {batchMode ? '单条模式' : '批量模式'}
              </Button>
            )}
            {!editing && hasSavedConfig && (
              <Button type="button" variant="outline" size="sm" onClick={restoreLastConfig}>
                一键还原配置
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            {/* 平台选择 */}

            <FormField
              control={form.control}
              name="platform"
              render={({ field }) => (
                <FormItem>
                  <Select
                    disabled={!!editing}
                    value={field.value}
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {videoPlatforms?.map(p => (
                        <SelectItem key={p.value} value={p.value}>
                          <div className="flex items-center justify-center gap-2">
                            <div className="h-4 w-4">{p.logo()}</div>
                            <span>{p.label}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage style={{ display: 'none' }} />
                </FormItem>
              )}
            />
            {/* 链接输入 / 上传框 */}
            <FormField
              control={form.control}
              name="video_url"
              render={({ field }) => (
                <FormItem className="flex-1">
                  {platform === 'local' ? (
                    <>
                      <Input disabled={!!editing} placeholder="请输入本地视频路径" {...field} />
                    </>
                  ) : (
                    batchMode ? (
                      <Textarea
                        disabled={!!editing}
                        className="min-h-24"
                        placeholder="每行粘贴一个视频链接；也支持一次粘贴多条链接"
                        {...field}
                      />
                    ) : (
                      <Input
                        disabled={!!editing}
                        placeholder={platform === 'xiaohongshu' ? '粘贴小红书链接或完整分享文案' : '请输入视频网站链接'}
                        {...field}
                      />
                    )
                  )}
                  <FormMessage style={{ display: 'none' }} />
                </FormItem>
              )}
            />
          </div>

          <FormField
            control={form.control}
            name="video_url"
            render={({ field }) => (
              <FormItem className="flex-1">
                {platform === 'local' && (
                  <>
                    <div
                      className="hover:border-primary mt-2 flex h-40 cursor-pointer items-center justify-center rounded-md border-2 border-dashed border-gray-300 transition-colors"
                      onDragOver={e => {
                        e.preventDefault()
                        e.stopPropagation()
                      }}
                      onDrop={e => {
                        e.preventDefault()
                        const file = e.dataTransfer.files?.[0]
                        if (file) handleFileUpload(file, field.onChange)
                      }}
                      onClick={() => {
                        const input = document.createElement('input')
                        input.type = 'file'
                        input.accept = 'video/*'
                        input.onchange = e => {
                          const file = (e.target as HTMLInputElement).files?.[0]
                          if (file) handleFileUpload(file, field.onChange)
                        }
                        input.click()
                      }}
                    >
                      {isUploading ? (
                        <p className="text-center text-sm text-blue-500">上传中，请稍候…</p>
                      ) : uploadSuccess ? (
                        <p className="text-center text-sm text-green-500">上传成功！</p>
                      ) : (
                        <p className="text-center text-sm text-gray-500">
                          拖拽文件到这里上传 <br />
                          <span className="text-xs text-gray-400">或点击选择文件</span>
                        </p>
                      )}
                    </div>
                  </>
                )}
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="grid grid-cols-2 gap-2">
            {/* 模型选择 */}
            {

             modelList.length>0?(     <FormField
               className="w-full"
               control={form.control}
               name="model_name"
               render={({ field }) => (
                 <FormItem>
                   <SectionHeader title="模型选择" tip="不同模型效果不同，建议自行测试" />
                   <Select
                     onOpenChange={()=>{
                       loadEnabledModels()
                     }}
                     value={field.value}
                     onValueChange={field.onChange}
                     defaultValue={field.value}
                   >
                     <FormControl>
                       <SelectTrigger className="w-full min-w-0 truncate">
                         <SelectValue />
                       </SelectTrigger>
                     </FormControl>
                     <SelectContent>
                       {modelList.map(m => (
                         <SelectItem key={m.id} value={m.model_name}>
                           {m.model_name}
                         </SelectItem>
                       ))}
                     </SelectContent>
                   </Select>
                   <FormMessage />
                 </FormItem>
               )}
             />): (
               <FormItem>
                 <SectionHeader title="模型选择" tip="不同模型效果不同，建议自行测试" />
                  <Button type={'button'} variant={
                    'outline'
                  } onClick={()=>{goModelAdd()}}>请先添加模型</Button>
                 <FormMessage />
               </FormItem>
             )
            }

            {/* 笔记风格 */}
            <FormField
              className="w-full"
              control={form.control}
              name="style"
              render={({ field }) => (
                <FormItem>
                  <SectionHeader title="笔记风格" tip="选择生成笔记的呈现风格" />
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full min-w-0 truncate">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {noteStyles.map(({ label, value }) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          {/* 字幕优先：默认使用平台字幕，无字幕时自动转写；勾选后强制使用语音转写 */}
          <SectionHeader
            title="语音转文字"
            tip="默认优先使用 B 站/YouTube 提供的字幕；没有字幕时自动进行语音转写"
          />
          <FormField
            control={form.control}
            name="force_transcription"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-center gap-2">
                  <Checkbox
                    checked={field.value ?? false}
                    onCheckedChange={value => field.onChange(value === true)}
                  />
                  <FormLabel>强制语音转写（忽略平台字幕）</FormLabel>
                </div>
                <FormMessage />
              </FormItem>
            )}
          />
          {videoUnderstandingEnabled && (
            <FormField
              control={form.control}
              name="visual_model_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>视觉模型（可选）</FormLabel>
                  <Select
                    value={field.value || "__same_model__"}
                    onValueChange={value => field.onChange(value === "__same_model__" ? "" : value)}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full min-w-0 truncate">
                        <SelectValue placeholder="不指定，使用当前模型直接看图" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="__same_model__">不指定（当前模型直接处理图片）</SelectItem>
                      {modelList.filter(model => model.capabilities?.supports_vision === true).map(model => (
                        <SelectItem key={`${model.provider_id}-${model.model_name}`} value={model.model_name}>
                          {model.model_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    指定后由视觉模型分析关键帧，当前模型只处理字幕和视觉摘要。
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}

          {/* 视频理解 */}
          <SectionHeader title="视频理解" tip="将视频截图发给多模态模型辅助分析" />
          <div className="flex flex-col gap-2">
            <FormField
              control={form.control}
              name="video_understanding"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormLabel>启用</FormLabel>
                    <Checkbox
                      checked={videoUnderstandingEnabled}
                      onCheckedChange={v => form.setValue('video_understanding', v)}
                    />
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-4">
              {/* 采样间隔 */}
              <FormField
                control={form.control}
                name="video_interval"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>采样间隔（秒）</FormLabel>
                    <Input disabled={!videoUnderstandingEnabled} type="number" {...field} />
                    <FormMessage />
                  </FormItem>
                )}
              />
              {/* 拼图大小 */}
              <FormField
                control={form.control}
                name="grid_size"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>拼图尺寸（列 × 行）</FormLabel>
                    <div className="flex items-center space-x-2">
                      <Input
                        disabled={!videoUnderstandingEnabled}
                        type="number"
                        value={field.value?.[0] || 3}
                        onChange={e => field.onChange([+e.target.value, field.value?.[1] || 3])}
                        className="w-16"
                      />
                      <span>x</span>
                      <Input
                        disabled={!videoUnderstandingEnabled}
                        type="number"
                        value={field.value?.[1] || 3}
                        onChange={e => field.onChange([field.value?.[0] || 3, +e.target.value])}
                        className="w-16"
                      />
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <Alert variant="warning" className="text-sm">
              <AlertDescription>
                <strong>提示：</strong>视频理解功能必须使用多模态模型。
              </AlertDescription>
            </Alert>
          </div>

          {/* 笔记格式 */}
          <FormField
            control={form.control}
            name="format"
            render={({ field }) => (
              <FormItem>
                <SectionHeader title="笔记格式" tip="选择要包含的笔记元素" />
                <CheckboxGroup
                  value={field.value}
                  onChange={field.onChange}
                  disabledMap={{
                    link: platform === 'local',
                    screenshot: !videoUnderstandingEnabled,
                  }}
                />
                <FormMessage />
              </FormItem>
            )}
          />

          {/* 备注 */}
          <FormField
            control={form.control}
            name="extras"
            render={({ field }) => (
              <FormItem>
                <SectionHeader title="备注" tip="可在 Prompt 结尾附加自定义说明" />
                <Textarea placeholder="笔记需要罗列出 xxx 关键点…" {...field} />
                <FormMessage />
              </FormItem>
            )}
          />
        </form>
      </Form>
    </div>
  )
}

export default NoteForm

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

import type { Classroom, Teacher } from '@/types/archive'
import type { ClassItem } from '@/types/course'
import type { Lesson } from '@/types/lesson'
import { datePart, isoAt, minutesOfClock, timePart } from '@/utils/timetable'

const props = defineProps<{
  modelValue: boolean
  /** create=新增单次课；move=给已有课次改时间/教室/老师（PATCH 不改班级） */
  mode: 'create' | 'move'
  lesson: Lesson | null
  classes: ClassItem[]
  classrooms: Classroom[]
  teachers: Teacher[]
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [
    payload: {
      class_id?: string
      classroom_id: string
      teacher_id: string
      start_at: string
      end_at: string
    },
  ]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const formRef = ref<FormInstance>()
const form = reactive({
  class_id: '',
  classroom_id: '',
  teacher_id: '',
  date: '',
  start: '',
  end: '',
})

const rules: FormRules<typeof form> = {
  class_id: [{ required: true, message: '请选择班级', trigger: 'change' }],
  classroom_id: [{ required: true, message: '请选择教室', trigger: 'change' }],
  teacher_id: [{ required: true, message: '请选择老师', trigger: 'change' }],
  date: [{ required: true, message: '请选择日期', trigger: 'change' }],
  start: [{ required: true, message: '请选择开始时间', trigger: 'change' }],
  end: [
    { required: true, message: '请选择结束时间', trigger: 'change' },
    {
      validator: (_rule, value: string, callback) => {
        if (value && form.start && minutesOfClock(value) <= minutesOfClock(form.start)) {
          callback(new Error('结束时间必须晚于开始时间'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
}

/** 弹窗每次打开都按来源重置，避免上次的残值串到这次 */
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    formRef.value?.clearValidate()
    if (props.mode === 'move' && props.lesson) {
      form.class_id = props.lesson.class_id
      form.classroom_id = props.lesson.classroom_id
      form.teacher_id = props.lesson.teacher_id
      form.date = datePart(props.lesson.start_at)
      form.start = timePart(props.lesson.start_at)
      form.end = timePart(props.lesson.end_at)
      return
    }
    form.class_id = ''
    form.classroom_id = ''
    form.teacher_id = ''
    form.date = ''
    form.start = '18:00'
    form.end = '19:30'
  },
)

/** 建班链路里选了班级就默认带出它的教室/老师，少点两下 */
function syncFromClass(classId: string): void {
  if (props.mode !== 'create') return
  const target = props.classes.find((item) => item.id === classId)
  if (!target) return
  form.classroom_id = target.classroom_id
  form.teacher_id = target.teacher_id
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  emit('submit', {
    class_id: props.mode === 'create' ? form.class_id : undefined,
    classroom_id: form.classroom_id,
    teacher_id: form.teacher_id,
    start_at: isoAt(form.date, minutesOfClock(form.start)),
    end_at: isoAt(form.date, minutesOfClock(form.end)),
  })
}

const durationText = computed(() => {
  if (!form.start || !form.end) return ''
  const diff = minutesOfClock(form.end) - minutesOfClock(form.start)
  return diff > 0 ? `${diff} 分钟` : ''
})

/** 跨零点时 el-time-picker 只给 HH:mm，这里提示一下，避免存进去变成负时长 */
const crossesMidnight = computed(
  () => props.mode === 'move' && props.lesson
    ? datePart(props.lesson.end_at) !== datePart(props.lesson.start_at)
    : false,
)
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="mode === 'create' ? '新增单次课' : `调课 · ${lesson?.class_name ?? ''}`"
    width="560px"
  >
    <el-alert
      v-if="crossesMidnight"
      type="info"
      show-icon
      :closable="false"
      title="该课次跨零点，表单只能填当天时段；如需跨天请先取消再分别排课。"
      class="lesson-form__hint"
    />

    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="班级" prop="class_id">
        <el-select
          v-model="form.class_id"
          :disabled="mode === 'move'"
          placeholder="选择班级"
          filterable
          style="width: 100%"
          @change="syncFromClass"
        >
          <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <div v-if="mode === 'move'" class="lesson-form__tip">调课不换班级；要换班请先在班级页移出再加入</div>
      </el-form-item>

      <el-form-item label="日期" prop="date">
        <el-date-picker
          v-model="form.date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择日期"
          style="width: 100%"
        />
      </el-form-item>

      <el-form-item label="时间" required>
        <div class="lesson-form__range">
          <el-form-item prop="start">
            <el-time-picker
              v-model="form.start"
              value-format="HH:mm"
              format="HH:mm"
              placeholder="开始"
              style="width: 100%"
            />
          </el-form-item>
          <span class="lesson-form__sep">至</span>
          <el-form-item prop="end">
            <el-time-picker
              v-model="form.end"
              value-format="HH:mm"
              format="HH:mm"
              placeholder="结束"
              style="width: 100%"
            />
          </el-form-item>
        </div>
        <div v-if="durationText" class="lesson-form__tip">时长 {{ durationText }}</div>
      </el-form-item>

      <el-form-item label="教室" prop="classroom_id">
        <el-select v-model="form.classroom_id" placeholder="选择教室" filterable style="width: 100%">
          <el-option
            v-for="item in classrooms"
            :key="item.id"
            :label="`${item.name}（容量 ${item.capacity}）`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="老师" prop="teacher_id">
        <el-select v-model="form.teacher_id" placeholder="选择老师" filterable style="width: 100%">
          <el-option
            v-for="item in teachers"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">
        {{ mode === 'create' ? '创建' : '保存调课' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.lesson-form__hint {
  margin-bottom: 12px;
}

.lesson-form__range {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.lesson-form__sep {
  color: #909399;
  font-size: 13px;
}

.lesson-form__tip {
  width: 100%;
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.6;
  color: #909399;
}
</style>

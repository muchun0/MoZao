import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useProjectStore = defineStore('project', () => {
  const currentProjectId = ref('')
  const projectDetail = ref(null)
  const chapters = ref([])
  const outline = ref(null)
  const world = ref(null)

  return { currentProjectId, projectDetail, chapters, outline, world }
})

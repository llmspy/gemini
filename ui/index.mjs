import { ref, computed, inject, onUnmounted, toRef, watch } from 'vue'

let ext = null

async function loadFilestores() {
    const api = await ext.getJson("/filestores")
    if (api.error) {
        ext.setError(api.error)
        return
    }
    ext.setState({ filestores: api.response })
}

const FileStoreList = {
    template: `
        <div class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <div class="flex justify-between items-center mb-8">
                <div>
                   <h1 class="text-2xl font-bold text-gray-900 dark:text-white">File Stores</h1>
                   <p class="text-sm text-gray-500 dark:text-gray-400">Manage your file stores for Gemini search grounding</p>
                </div>
                <button type="button" @click="showCreate = true" class="inline-flex items-center px-4 py-2 border border-transparent rounded-full shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500">
                    New Store
                </button>
            </div>

            <div v-if="showCreate" class="mb-8 bg-gray-50 dark:bg-gray-800 rounded-lg p-6 border border-gray-200 dark:border-gray-700">
                <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">Create New Store</h3>
                <form @submit.prevent="createStore" class="flex gap-4">
                    <div class="flex-grow">
                        <label for="storeName" class="sr-only">Store Name</label>
                        <input type="text" id="storeName" v-model="newStoreName" placeholder="e.g. Project Documentation" class="block w-full rounded-md border-gray-300 dark:border-gray-600 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm dark:bg-gray-700 dark:text-white p-2">
                    </div>
                    <button type="submit" :disabled="loading || !newStoreName.trim()" class="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50">
                        <span v-if="loading">Creating...</span>
                        <span v-else>Create</span>
                    </button>
                    <button type="button" @click="showCreate = false" class="inline-flex justify-center py-2 px-4 border border-gray-300 dark:border-gray-600 shadow-sm text-sm font-medium rounded-md text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500">
                        Cancel
                    </button>
                </form>
            </div>

            <div class="bg-white dark:bg-gray-800 shadow overflow-hidden sm:rounded-md">
                <ul class="divide-y divide-gray-200 dark:divide-gray-700">
                    <li v-for="store in filestores" :key="store.id">
                        <button @click="$emit('select', store.id)" type="button" class="w-full block hover:bg-gray-50 dark:hover:bg-gray-700 transition duration-150 ease-in-out">
                            <div class="px-4 py-4 sm:px-6">
                                <div class="flex items-center justify-between">
                                    <p class="text-sm font-medium text-blue-600 dark:text-blue-400 truncate">{{ store.displayName }}</p>
                                    <div class="ml-2 flex-shrink-0 flex">
                                        <p class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
                                            {{ store.activeDocumentsCount || 0 }} docs
                                        </p>
                                    </div>
                                </div>
                                <div class="mt-2 sm:flex sm:justify-between">
                                    <div class="sm:flex">
                                        <p class="flex items-center text-sm text-gray-500 dark:text-gray-400">
                                            Created {{ $fmt.date(store.createdAt) }}
                                        </p>
                                    </div>
                                    <div class="mt-2 flex items-center text-sm text-gray-500 dark:text-gray-400 sm:mt-0">
                                        <p>
                                            {{ $fmt.bytes(store.sizeBytes || 0) }}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </button>
                    </li>
                    <li v-if="filestores.length === 0" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
                        No file stores found. Create one to get started.
                    </li>
                </ul>
            </div>
        </div>
    `,
    emits: ['select'],
    setup() {
        const filestores = toRef(ext.state, 'filestores')
        const showCreate = ref(false)
        const newStoreName = ref('')
        const loading = ref(false)

        async function createStore() {
            if (!newStoreName.value.trim()) return
            loading.value = true
            try {
                await ext.postJson("/filestores", {
                    displayName: newStoreName.value
                })
                await loadFilestores()
                showCreate.value = false
                newStoreName.value = ''
            } finally {
                loading.value = false
            }
        }

        function formatDate(date) {
            if (!date) return ''
            return new Date(date).toLocaleDateString()
        }

        return {
            ext,
            showCreate,
            newStoreName,
            loading,
            filestores,
            createStore,
            formatDate,
        }
    }
}

const FileStoreDetails = {
    props: ['storeId'],

    template: `
        <div class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8" v-if="store">
            <div class="flex justify-between items-center mb-8">
                 <div class="flex items-center gap-4">
                     <button type="button"
                        @click="$emit('back')"
                        class="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                     >
                        <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                     </button>
                     <div>
                        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">{{ store.displayName }}</h1>
                        <p class="text-sm text-gray-500 dark:text-gray-400">Upload documents to this store</p>
                     </div>
                 </div>
                 <div class="flex items-center gap-2">
                     <input type="file" ref="fileInput" class="hidden" multiple @change="handleFileUpload">
                     <button type="button" 
                        @click="fileInput.click()"
                        :disabled="uploading"
                        class="inline-flex items-center px-4 py-2 border border-transparent rounded-full shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed">
                        <span v-if="uploading">Uploading...</span>
                        <span v-else>Upload Documents</span>
                     </button>
                 </div>
            </div>
            
            <div 
                @drop.prevent="onDrop" 
                @dragover.prevent="dragover = true" 
                @dragleave.prevent="dragover = false"
                @click="fileInput.click()"
                :class="{'border-blue-500 bg-blue-50 dark:bg-blue-900/20': dragover}"
                class="group relative transition-colors duration-200 text-center py-12 bg-gray-50 dark:bg-gray-800/50 rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-700 mb-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800">
                <div class="mx-auto h-12 w-12 text-gray-400 text-5xl mb-4">📄</div>
                 <h3 class="mt-2 text-sm font-medium text-gray-900 dark:text-white">
                    <span class="group-hover:text-blue-600">Upload a file</span> or drag and drop
                 </h3>
                 <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">Upload PDFs, Text files or Markdown to get started.</p>
            </div>

            <div v-if="uploadedDocs.length > 0" class="mb-8">
                <div class="flex justify-between">
                    <h3 class="text-lg font-medium text-gray-900 dark:text-white mb-4">Recent Uploads</h3>
                    <button type="button" @click="uploadedDocs=[]" class="pr-2 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:dark:text-gray-200">clear</button>
                </div>
                <div class="bg-white dark:bg-gray-800 shadow overflow-hidden sm:rounded-md">
                    <ul class="divide-y divide-gray-200 dark:divide-gray-700">
                        <li v-for="doc in uploadedDocs" :key="doc.id">
                            <div class="px-4 py-4 sm:px-6 flex items-center justify-between">
                                <div class="flex items-center truncate">
                                    <a :href="doc.url + '?download'" class="text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 truncate" :title="'Download ' + doc.displayName">{{ doc.displayName }}</a>
                                </div>
                                <div class="flex-shrink-0 flex items-center gap-2">
                                    <span v-if="doc.error" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200" :title="doc.error">
                                        Failed
                                    </span>
                                    <span v-else-if="doc.uploadedAt" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
                                        Completed
                                    </span>
                                    <span v-else-if="doc.startedAt" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200">
                                        Processing
                                    </span>
                                    <span v-else class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                                        Pending
                                    </span>
                                </div>
                            </div>
                        </li>
                    </ul>
                </div>
            </div>

            <div class="bg-white dark:bg-gray-800 shadow overflow-hidden sm:rounded-md mb-8">
               <div class="px-4 py-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-900/50">
                   <div class="relative max-w-xs w-full">
                       <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                          <svg class="h-4 w-4 text-gray-400" viewBox="0 0 20 20" fill="currentColor">
                              <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
                          </svg>
                       </div>
                       <input type="text" v-model.lazy="q" placeholder="Search" 
                           class="block w-full pl-9 pr-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md leading-5 bg-white dark:bg-gray-700 placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm text-gray-900 dark:text-white">
                   </div>
                   <div class="flex items-center gap-4 text-sm font-medium">
                       <button v-if="page > 1" @click="page--; loadDocuments()" type="button" class="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 disabled:text-gray-400 disabled:cursor-not-allowed">
                           &larr; previous
                       </button>
                       <button v-if="docs.length >= 10" @click="page++; loadDocuments()" type="button" class="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 disabled:text-gray-400 disabled:cursor-not-allowed">
                           next &rarr;
                       </button>
                   </div>
               </div>
               <ul class="divide-y divide-gray-200 dark:divide-gray-700">
                   <li v-for="doc in docs" :key="doc.id">
                       <div class="px-4 py-4 sm:px-6 flex items-center justify-between">
                            <div class="flex items-center min-w-0 flex-1">
                                <div class="min-w-0 flex-1 mr-4">
                                   <a :href="doc.url + '?download'" class="text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 truncate" :title="'Download ' + doc.displayName">{{ doc.displayName }}</a>
                                   <div class="flex items-center mt-1">
                                       <span class="flex-shrink-0 text-xs text-gray-500 dark:text-gray-400">
                                           {{ $fmt.bytes(doc.size) }} &middot; {{ $fmt.date(doc.uploadedAt || doc.createdAt) }}
                                       </span>
                                   </div>
                                </div>
                            </div>
                            <div class="flex-shrink-0 flex items-center gap-2">
                                <span v-if="doc.error" class="text-red-600" :title="doc.error">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10s10-4.48 10-10S17.52 2 12 2m1 15h-2v-2h2zm0-4h-2V7h2z"/></svg>
                                </span>
                                <span v-else-if="doc.state === 'STATE_ACTIVE'" class="text-green-600" title="Active">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="currentColor" fill-rule="evenodd" d="M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18m-.232-5.36l5-6l-1.536-1.28l-4.3 5.159l-2.225-2.226l-1.414 1.414l3 3l.774.774z" clip-rule="evenodd"/></svg>
                                </span>
                                <span v-else-if="doc.state" class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">{{ doc.state }}</span>
                            </div>
                       </div>
                   </li>
                   <li v-if="docs.length === 0 && !docsLoading" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
                       No documents found.
                   </li>
               </ul>

            </div>
            
            <div class="flex justify-between items-center dark:border-gray-700">
                <div>
                   <h3 class="text-lg font-medium text-gray-900 dark:text-white">Delete {{store.displayName}}</h3>
                   <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">Permanently delete this file store and all its documents.</p>
                </div>
                <button type="button"
                    @click="deleteStore"
                    class="inline-flex items-center px-4 py-2 border border-transparent rounded-full shadow-sm text-sm font-medium text-white bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
                >
                    Delete Store
                </button>
            </div>
        </div>
        <div v-else-if="loading" class="p-8 text-center text-gray-500">Loading store...</div>
        <div v-else class="p-8 text-center text-red-500">Store not found</div>
    `,
    emits: ['select', 'back'],
    setup(props, { emit }) {
        const store = computed(() => ext.state.filestores?.find(s => s.id == props.storeId))
        const loading = ref(false)
        const fileInput = ref(null)
        const uploading = ref(false)
        const dragover = ref(false)
        const categories = ref([])
        const uploadedDocs = ref([])
        const docs = ref([])
        const docsLoading = ref(false)
        const page = ref(1)
        const q = ref('')
        let pollTimer = null
        let lastRequestId = 0

        async function loadDocuments() {
            const requestId = ++lastRequestId
            docsLoading.value = true
            try {
                const params = new URLSearchParams({
                    filestoreId: props.storeId,
                    take: 10,
                    skip: (page.value - 1) * 10,
                    sort: '-uploadedAt',
                })
                if (q.value) params.append('q', q.value)

                const api = await ext.getJson(`/documents?${params.toString()}`)
                if (requestId !== lastRequestId) return

                if (api.error) {
                    console.error("Failed to load docs", api.error)
                    return
                }
                docs.value = api.response
            } finally {
                if (requestId === lastRequestId) {
                    docsLoading.value = false
                }
            }
        }

        async function loadDocumentCategories() {
            const api = await ext.getJson(`/filestores/${props.storeId}/categories`)
            if (api.error) {
                ext.setError(api.error)
            }
            categories.value = api.response || []
        }

        async function refresh() {
            await Promise.all([
                loadDocumentCategories(),
                loadDocuments(),
            ])
        }

        watch(() => props.storeId, () => {
            uploadedDocs.value = []
            page.value = 1
            q.value = ''
            refresh()
        }, { immediate: true })

        watch(q, () => {
            page.value = 1
            loadDocuments()
        })

        function formatDate(date) {
            if (!date) return ''
            return new Date(date).toLocaleDateString() + ' ' + new Date(date).toLocaleTimeString()
        }

        async function handleFileUpload(e) {
            const files = e.target.files
            if (!files || files.length === 0) return
            await uploadFiles(files)
        }

        async function onDrop(e) {
            dragover.value = false
            const files = e.dataTransfer.files
            if (!files || files.length === 0) return
            await uploadFiles(files)
        }

        async function uploadFiles(files) {
            uploading.value = true
            try {
                const formData = new FormData()
                for (let i = 0; i < files.length; i++) {
                    formData.append('file', files[i])
                }

                const res = await ext.postForm(`/filestores/${store.value.id}/upload`, { body: formData })
                if (!res.ok) throw new Error(res.statusText)
                const newDocs = await res.json()

                // Add new docs to the list, filtering out any that might already be there (unlikely but safe)
                const newDocIds = new Set(newDocs.map(d => d.id))
                uploadedDocs.value = [...newDocs, ...uploadedDocs.value.filter(d => !newDocIds.has(d.id))]

                startPolling()
                await loadFilestores()
                loadDocuments() // Refresh the main list too
            } catch (e) {
                console.error("Upload failed", e)
                alert("Upload failed: " + (e.message || "Unknown error"))
            } finally {
                uploading.value = false
                if (fileInput.value) fileInput.value.value = ''
            }
        }

        function startPolling() {
            if (pollTimer) return
            poll()
        }

        async function poll() {
            const pendingAuthDocs = uploadedDocs.value.filter(d => !d.uploadedAt && !d.error)
            if (pendingAuthDocs.length === 0) {
                pollTimer = null
                return
            }

            try {
                const ids = pendingAuthDocs.filter(d => d.id).map(d => d.id).join(',')
                const api = await ext.getJson(`/documents?ids_in=${ids}`)
                if (api.error) {
                    ext.setError(api.error)
                    return
                }
                const updatedDocs = api.response

                // Update local docs
                updatedDocs.forEach(updated => {
                    const idx = uploadedDocs.value.findIndex(d => d.id === updated.id)
                    if (idx !== -1) {
                        uploadedDocs.value[idx] = updated
                    }
                })

                // If any still pending, schedule next poll
                // Also check if any *just* completed/failed, we might want to refresh filestores stats
                const stillPending = updatedDocs.some(d => !d.uploadedAt && !d.error)

                if (stillPending) {
                    pollTimer = setTimeout(poll, 2000)
                } else {
                    pollTimer = null
                    await loadFilestores() // Final refresh
                    loadDocuments() // Refresh the main list
                    setTimeout(refresh, 2000)
                }

            } catch (e) {
                console.error("Polling failed", e)
                pollTimer = setTimeout(poll, 5000) // Retry later on error
            }
        }

        onUnmounted(() => {
            if (pollTimer) clearTimeout(pollTimer)
        })

        async function deleteStore() {
            if (!store.value) return
            if (!confirm(`Are you sure you want to delete "${store.value.displayName}"? This cannot be undone.`)) return

            const api = await ext.deleteJson("/filestores/" + store.value.id)
            if (api.error) {
                ext.setError(api.error)
            } else {
                await loadFilestores()
                emit('back')
            }
        }

        return { store, deleteStore, loading, fileInput, handleFileUpload, uploading, onDrop, dragover, uploadedDocs, docs, page, q, loadDocuments, docsLoading, formatDate }
    }
}

const GeminiPage = {
    template: `
        <div class="h-full bg-white dark:bg-gray-900 overflow-y-auto">
            <div class="m-2">
                <ErrorViewer />
            </div>
            <component :is="activeComponent" v-bind="componentProps" @select="onSelect" @back="onBack" />
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const route = ctx.router.currentRoute

        const activeComponent = computed(() => {
            if (route.value.params.id) return FileStoreDetails
            return FileStoreList
        })

        const componentProps = computed(() => {
            if (route.value.params.id) return { storeId: route.value.params.id }
            return {}
        })

        function onSelect(storeId) {
            ctx.to('/gemini/filestores/' + storeId)
        }

        function onBack() {
            ctx.to('/gemini')
        }

        return { activeComponent, componentProps, onSelect, onBack }
    }
}

export default {
    install(ctx) {
        ext = ctx.scope('gemini')

        ctx.setLeftIcons({
            gemini: {
                component: {
                    template: `<svg @click="$ctx.togglePath('/gemini')" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linejoin="round" stroke-width="1.5" d="M3 12a9 9 0 0 0 9-9a9 9 0 0 0 9 9a9 9 0 0 0-9 9a9 9 0 0 0-9-9Z"/></svg>`
                },
                isActive({ path }) { return path.startsWith('/gemini') }
            }
        })

        // Define routes with /gemini prefix to match ext.to() behavior
        ctx.routes.push(
            { path: '/gemini', component: GeminiPage, meta: { title: 'Gemini' } },
            { path: '/gemini/filestores/:id', component: GeminiPage, meta: { title: 'File Store' } }
        )

        ext.setState({
            filestores: []
        })
    },

    async load(ctx) {
        await loadFilestores()
    }
}

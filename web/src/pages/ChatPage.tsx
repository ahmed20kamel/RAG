import { useCallback, useEffect, useMemo, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Composer } from '@/components/chat/Composer'
import { AssistantMessage, UserMessage } from '@/components/chat/Message'
import { StageIndicator } from '@/components/chat/StageIndicator'
import { EmptyState, IconButton, Skeleton } from '@/components/ui/primitives'
import { Icon, type IconName } from '@/components/ui/Icon'
import { useCurrentUser } from '@/hooks/useAuth'
import { usePreferences } from '@/state/preferences'
import { useAsk, StoppedError } from '@/hooks/useAsk'
import { useHotkeys } from '@/hooks/useHotkeys'
import { useTranslation } from '@/hooks/useTranslation'
import { useLibraryStats } from '@/hooks/useDocuments'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import { newId, useConversations, type ChatMessage, type Feedback } from '@/state/conversations'
import type { ChatPicture } from '@/types/api'
import '@/components/chat/chat.css'

interface Starter {
  icon: IconName
  title: string
  hint: string
  prompt: string
}

/** What people most often come for: figures, deadlines, a spreadsheet, an assessment. */
const STARTERS_AR: Starter[] = [
  { icon: 'database', title: 'القيم والمبالغ', hint: 'قيمة العقد وشروط الدفع', prompt: 'ما قيمة العقد وما شروط الدفع؟' },
  { icon: 'clock', title: 'المدد والتأخير', hint: 'مدة التنفيذ وغرامات التأخير', prompt: 'ما مدة التنفيذ وغرامة التأخير؟' },
  { icon: 'layers', title: 'جدول Excel', hint: 'جدول الدفعات جاهز للتحميل', prompt: 'اعمل ملف اكسيل بجدول الدفعات في العقد' },
  { icon: 'shield', title: 'تحليل الموقف', hint: 'الوقائع والمخاطر والتوصيات', prompt: 'حلّل موقفنا في هذا الملف: الوقائع الثابتة، نقاط القوة، المخاطر، والتوصيات' },
]
const STARTERS_EN: Starter[] = [
  { icon: 'database', title: 'Values and amounts', hint: 'Contract value and payment terms', prompt: 'What is the contract value and what are the payment terms?' },
  { icon: 'clock', title: 'Durations and delay', hint: 'Execution period and delay penalties', prompt: 'What is the execution period and the delay penalty?' },
  { icon: 'layers', title: 'Excel table', hint: 'The payment schedule, ready to download', prompt: 'make an excel file with the payment schedule in the contract' },
  { icon: 'shield', title: 'Assess our position', hint: 'Facts, risks and recommendations', prompt: 'Assess our position in this file: established facts, strengths, risks and recommendations' },
]

export function ChatPage() {
  const { t, language } = useTranslation()
  const [params, setParams] = useSearchParams()

  const conversations = useConversations((state) => state.conversations)
  const activeId = useConversations((state) => state.activeId)
  const create = useConversations((state) => state.create)
  const select = useConversations((state) => state.select)
  const appendMessage = useConversations((state) => state.appendMessage)
  const updateMessage = useConversations((state) => state.updateMessage)
  const dropMessagesFrom = useConversations((state) => state.dropMessagesFrom)

  const { pending, stages, load, startedAt, ask, stop } = useAsk()
  const stats = useLibraryStats()
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)

  const conversation = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  )

  // A document filter arrives as ?document=<id> from the document viewer, so "ask about
  // this document" lands here already scoped to it.
  const documentFilter = params.get('document')

  useHotkeys([
    { key: 'k', ctrlOrMeta: true, whileTyping: true, handler: () => create() },
    { key: '/', handler: () => composerRef.current?.focus() },
  ])

  /** Only auto-scrolls while the reader is already at the bottom. */
  useEffect(() => {
    if (!atBottom.current) return
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [conversation?.messages.length, stages.length])

  const onScroll = useCallback(() => {
    const node = scrollRef.current
    if (!node) return
    atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80
  }, [])

  const run = useCallback(
    async (conversationId: string, question: string, fresh = false, picture?: ChatPicture) => {
      const placeholder: ChatMessage = {
        id: newId(),
        role: 'assistant',
        content: '',
        createdAt: Date.now(),
      }

      try {
        const response = await ask({
          question,
          document_ids: documentFilter ? [documentFilter] : null,
          conversation_id: conversationId,
          fresh,
          deep: usePreferences.getState().deepThinking,
          image_text: picture?.text || null,
          image_marked: picture?.marked || null,
          image_vision: picture?.vision || null,
        })
        if (!response) return
        appendMessage(conversationId, { ...placeholder, content: response.answer, response })
      } catch (error) {
        if (error instanceof StoppedError) {
          appendMessage(conversationId, {
            ...placeholder,
            content: '',
            stopped: true,
            error: t('chat.stopped'),
          })
          return
        }
        const detail = error instanceof ApiError ? error.message : t('errors.generic')
        appendMessage(conversationId, { ...placeholder, error: detail })
        toast.error(t('errors.title'), detail)
      }
    },
    [ask, appendMessage, documentFilter, t],
  )

  const onSubmit = useCallback(
    (question: string, picture?: ChatPicture) => {
      const conversationId = activeId ?? create()
      appendMessage(conversationId, {
        id: newId(),
        role: 'user',
        content: question,
        createdAt: Date.now(),
        picture,
      })
      void run(conversationId, question, false, picture)
    },
    [activeId, appendMessage, create, run],
  )

  const onRegenerate = useCallback(
    (assistantMessageId: string) => {
      if (!conversation) return
      const index = conversation.messages.findIndex((m) => m.id === assistantMessageId)
      const question = [...conversation.messages.slice(0, index)].reverse().find((m) => m.role === 'user')
      if (!question) return
      dropMessagesFrom(conversation.id, assistantMessageId)
      void run(conversation.id, question.content, true, question.picture)
    },
    [conversation, dropMessagesFrom, run],
  )

  const onFeedback = useCallback(
    (messageId: string, feedback: Feedback) => {
      if (!conversation) return
      updateMessage(conversation.id, messageId, { feedback })
      if (feedback) toast.info(t('chat.feedbackSaved'))
      // Sent to the server too: a thumbs-down answer is not given out again.
      const answerId = conversation.messages.find((m) => m.id === messageId)?.response?.answer_id
      if (answerId) void chatApi.feedback(answerId, feedback).catch(() => undefined)
    },
    [conversation, updateMessage, t],
  )

  const emptyLibrary = stats.data && stats.data.documents === 0
  const messages = conversation?.messages ?? []
  const lastAssistantId = [...messages].reverse().find((m) => m.role === 'assistant')?.id

  return (
    <div className="chat">
      <section className="chat__main">
        <header className="chat__header">
          <h1 className="chat__title">{conversation?.title || t('chat.title')}</h1>
          {documentFilter && (
            <button
              type="button"
              className="chat__filter"
              onClick={() => {
                params.delete('document')
                setParams(params, { replace: true })
              }}
            >
              <Icon name="file" size={13} />
              {t('document.askAbout')}
              <Icon name="close" size={12} />
            </button>
          )}
          <div className="chat__header-spacer" />
          <IconButton icon="plus" label={t('chat.newChat')} onClick={() => create()} />
        </header>

        <div className="chat__scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="chat__thread">
            {messages.length === 0 && !pending && (
              <Welcome
                emptyLibrary={Boolean(emptyLibrary)}
                loading={stats.isLoading}
                starters={language === 'ar' ? STARTERS_AR : STARTERS_EN}
                onPick={(example) => {
                  if (!activeId) select(create())
                  onSubmit(example)
                }}
              />
            )}

            {messages.map((message) =>
              message.role === 'user' ? (
                <UserMessage key={message.id} message={message} />
              ) : (
                <AssistantMessage
                  key={message.id}
                  message={message}
                  onRegenerate={message.id === lastAssistantId ? () => onRegenerate(message.id) : undefined}
                  onChoose={message.id === lastAssistantId ? onSubmit : undefined}
                  onFeedback={
                    message.response ? (feedback) => onFeedback(message.id, feedback) : undefined
                  }
                  question={questionBefore(messages, message.id)}
                  onFiles={
                    message.response && conversation
                      ? (files) =>
                          updateMessage(conversation.id, message.id, {
                            response: { ...message.response!, files: [...(message.response!.files ?? []), ...files] },
                          })
                      : undefined
                  }
                />
              ),
            )}

            {pending && <StageIndicator stages={stages} load={load} startedAt={startedAt} />}
          </div>
        </div>

        <Composer ref={composerRef} onSubmit={onSubmit} onStop={stop} pending={pending} />
      </section>
    </div>
  )
}

function Welcome({
  emptyLibrary,
  loading,
  starters,
  onPick,
}: {
  emptyLibrary: boolean
  loading: boolean
  starters: Starter[]
  onPick: (example: string) => void
}) {
  const { t } = useTranslation()
  const { data: user } = useCurrentUser()
  const hour = new Date().getHours()
  const greeting = t(hour < 12 ? 'chat.greetMorning' : hour < 18 ? 'chat.greetAfternoon' : 'chat.greetEvening', {
    name: (user?.display_name || '').split(/\s+/)[0] || '',
  })

  if (loading) {
    return (
      <div className="welcome">
        <Skeleton width="60%" height="2rem" />
        <Skeleton width="100%" height="4rem" radius="14px" />
      </div>
    )
  }

  if (emptyLibrary) {
    return (
      <EmptyState
        icon="library"
        title={t('chat.emptyLibraryTitle')}
        body={t('chat.emptyLibraryBody')}
        action={
          <Link to="/upload" className="btn btn--primary btn--md">
            <Icon name="upload" size={16} />
            {t('nav.upload')}
          </Link>
        }
      />
    )
  }

  return (
    <div className="welcome">
      <span className="welcome__glyph">
        <Icon name="sparkles" size={26} />
      </span>
      <h2 className="welcome__title">{greeting}</h2>
      <p className="welcome__body">{t('chat.welcomePrompt')}</p>
      <div className="welcome__starters">
        {starters.map((starter) => (
          <button key={starter.title} type="button" className="starter" onClick={() => onPick(starter.prompt)}>
            <span className="starter__icon">
              <Icon name={starter.icon} size={18} />
            </span>
            <span className="starter__text">
              <strong>{starter.title}</strong>
              <small>{starter.hint}</small>
            </span>
          </button>
        ))}
      </div>
      <p className="welcome__note">{t('chat.welcomeNote')}</p>
    </div>
  )
}

/** The reader's question an answer replies to: the nearest user message before it. */
function questionBefore(messages: ChatMessage[], answerId: string): string {
  const index = messages.findIndex((m) => m.id === answerId)
  for (let i = index - 1; i >= 0; i -= 1) if (messages[i].role === 'user') return messages[i].content
  return ''
}

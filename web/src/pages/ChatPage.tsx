import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Composer } from '@/components/chat/Composer'
import { ConversationSidebar } from '@/components/chat/ConversationSidebar'
import { AssistantMessage, UserMessage } from '@/components/chat/Message'
import { StageIndicator } from '@/components/chat/StageIndicator'
import { EmptyState, IconButton, Skeleton } from '@/components/ui/primitives'
import { Icon } from '@/components/ui/Icon'
import { useAsk, StoppedError } from '@/hooks/useAsk'
import { useHotkeys } from '@/hooks/useHotkeys'
import { useIsTablet } from '@/hooks/useMediaQuery'
import { useTranslation } from '@/hooks/useTranslation'
import { useLibraryStats } from '@/hooks/useDocuments'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import { toast } from '@/state/toasts'
import { newId, useConversations, type ChatMessage, type Feedback } from '@/state/conversations'
import '@/components/chat/chat.css'

const EXAMPLES_AR = [
  'ما أهم بنود العقد المتعلقة بالتأخير؟',
  'من هم أطراف النزاع وما دور كل طرف؟',
  'اذكر التسلسل الزمني للأحداث بالتواريخ',
]
const EXAMPLES_EN = [
  'What are the key delay provisions in the contract?',
  'Who are the parties and what role does each play?',
  'List the sequence of events with dates',
]

export function ChatPage() {
  const { t, language } = useTranslation()
  const isTablet = useIsTablet()
  const [params, setParams] = useSearchParams()
  const [sidebarOpen, setSidebarOpen] = useState(false)

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
    async (conversationId: string, question: string, fresh = false) => {
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
    (question: string) => {
      const conversationId = activeId ?? create()
      appendMessage(conversationId, {
        id: newId(),
        role: 'user',
        content: question,
        createdAt: Date.now(),
      })
      void run(conversationId, question)
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
      void run(conversation.id, question.content, true)
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
      {!isTablet && <ConversationSidebar />}

      {isTablet && sidebarOpen && (
        <div className="chat__drawer" onClick={() => setSidebarOpen(false)}>
          <div onClick={(event) => event.stopPropagation()}>
            <ConversationSidebar onSelect={() => setSidebarOpen(false)} />
          </div>
        </div>
      )}

      <section className="chat__main">
        <header className="chat__header">
          {isTablet && (
            <IconButton icon="menu" label={t('chat.conversations')} onClick={() => setSidebarOpen(true)} />
          )}
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
                examples={language === 'ar' ? EXAMPLES_AR : EXAMPLES_EN}
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
  examples,
  onPick,
}: {
  emptyLibrary: boolean
  loading: boolean
  examples: string[]
  onPick: (example: string) => void
}) {
  const { t } = useTranslation()

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
      <h2 className="welcome__title">{t('chat.welcomeTitle')}</h2>
      <p className="welcome__body">{t('chat.welcomeBody')}</p>
      <div className="welcome__examples">
        <span className="welcome__examples-label">{t('chat.examplesTitle')}</span>
        {examples.map((example) => (
          <button key={example} type="button" className="welcome__example" onClick={() => onPick(example)}>
            {example}
            <Icon name="chevronEnd" size={14} />
          </button>
        ))}
      </div>
    </div>
  )
}

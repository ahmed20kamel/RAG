import { useCallback, useMemo, useState } from 'react'
import { Badge, Button, IconButton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { Icon } from '@/components/ui/Icon'
import { AnswerDetails } from './AnswerDetails'
import { CorrectAnswerDialog } from './CorrectAnswerDialog'
import { ExportMenu } from './ExportMenu'
import { FileCards } from './FileCards'
import { LearningPrompt, TeachFromChatDialog } from './LearningPrompt'
import { SourceList, WebSourceList } from './SourceCard'
import { WhyThisAnswer } from './WhyThisAnswer'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { useCopy } from '@/hooks/useCopy'
import { renderMarkdown, markdownToPlainText } from '@/utils/markdown'
import { toast } from '@/state/toasts'
import type { ChatMessage, Feedback } from '@/state/conversations'
import type { ChatFile, SourceReference } from '@/types/api'

export function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="msg msg--user">
      <div className="msg__bubble">
        {message.picture && (
          <img src={message.picture.thumbnail} alt="" className="msg__picture" loading="lazy" />
        )}
        {message.content}
      </div>
    </div>
  )
}

export function AssistantMessage({
  message,
  onRegenerate,
  onFeedback,
  onChoose,
  question,
  onFiles,
}: {
  message: ChatMessage
  onRegenerate?: () => void
  onFeedback?: (feedback: Feedback) => void
  /** Sends one of the answer's offered choices as the next question. */
  onChoose?: (question: string) => void
  /** The question this answers — the title of a file made from it. */
  question?: string
  /** Files made from this answer, kept with the message. */
  onFiles?: (files: ChatFile[]) => void
}) {
  const { t, language } = useTranslation()
  const { copied, copy } = useCopy()
  const [activeCitation, setActiveCitation] = useState<number | null>(null)
  // One at a time: these are modal dialogs over the same message.
  const [dialog, setDialog] = useState<'teach' | 'correct' | 'why' | null>(null)
  const { data: currentUser } = useCurrentUser()

  const response = message.response
  const rendered = useMemo(() => renderMarkdown(message.content), [message.content])

  // Citations are plain buttons inside the rendered HTML, so one delegated listener
  // handles every one of them without re-rendering the answer for each.
  const onAnswerClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-citation]')
    if (!target) return
    const citation = Number(target.dataset.citation)
    if (!Number.isFinite(citation)) return
    setActiveCitation(citation)
    document
      .getElementById(`source-${citation}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  if (message.error) {
    return (
      <div className="msg msg--assistant">
        <Avatar />
        <div className="msg__column">
          <div className="msg__error" role="alert">
            <Icon name="alert" size={16} />
            <span>{message.error}</span>
          </div>
          {onRegenerate && (
            <Button size="sm" variant="ghost" icon="refresh" onClick={onRegenerate}>
              {t('common.retry')}
            </Button>
          )}
        </div>
      </div>
    )
  }

  const sources = response?.sources ?? []
  const webSources = response?.web_sources ?? []
  const grounded = response?.grounded ?? true
  const answerId = response?.answer_id ?? ''
  const mayTeach = can(currentUser, 'knowledge.propose')

  return (
    <div className="msg msg--assistant">
      <Avatar />
      <div className="msg__column">
        {!grounded && (
          <Badge tone="warning">
            <Icon name="info" size={12} />
            {t('chat.notGrounded')}
          </Badge>
        )}

        <div
          className="msg__answer"
          lang={language}
          onClick={onAnswerClick}
          dangerouslySetInnerHTML={{ __html: rendered.html }}
        />

        {message.stopped && <p className="chat-note">{t('chat.stopped')}</p>}

        {/* What the check of the answer found, said where the reader is looking: every
            figure in it was found in the sources, or which one was not. */}
        {response && grounded && sources.length > 0 && response.validation && (
          response.validation.unsupported_values.length > 0 ? (
            <p className="answer-check answer-check--warn" role="note">
              <Icon name="alert" size={13} />
              {t('chat.checkUnsupported', { values: response.validation.unsupported_values.slice(0, 3).join('، ') })}
            </p>
          ) : (
            <p className="answer-check" role="note">
              <Icon name="check" size={13} />
              {t('chat.checkVerified', { count: new Set(sources.map((s) => s.document_id)).size })}
            </p>
          )
        )}

        {(response?.files?.length ?? 0) > 0 && <FileCards files={response!.files!} />}

        {onChoose && (response?.choices?.length ?? 0) > 0 && (
          <div className="msg__choices" role="group" aria-label={t('chat.choices')}>
            {response!.choices!.map((choice) => (
              <Button
                key={choice.question}
                size="sm"
                variant="secondary"
                icon="document"
                onClick={() => onChoose(choice.question)}
              >
                {choice.label}
              </Button>
            ))}
          </div>
        )}

        <div className="msg__actions">
          <IconButton
            icon={copied ? 'check' : 'copy'}
            label={copied ? t('common.copied') : t('chat.copyAnswer')}
            size="sm"
            onClick={async () => {
              const ok = await copy(markdownToPlainText(message.content))
              if (!ok) toast.error(t('errors.copyFailed'))
            }}
          />
          {onRegenerate && (
            <IconButton icon="refresh" label={t('chat.regenerate')} size="sm" onClick={onRegenerate} />
          )}
          {response && response.grounded && onFiles && (response.files?.length ?? 0) === 0 && (
            <ExportMenu
              title={(question || t('chat.title')).replace(/[؟?]+$/, '')}
              markdown={message.content}
              response={response}
              onMade={onFiles}
            />
          )}
          {answerId && (
            <IconButton
              icon="info"
              label={t('why.title')}
              size="sm"
              onClick={() => setDialog('why')}
            />
          )}
          {answerId && mayTeach && (
            <>
              {/* Labelled, not icons: teaching is how the system learns, and an
                  unlabelled sparkle was not found by the people it was for. */}
              <Button variant="ghost" size="sm" icon="sparkles" onClick={() => setDialog('teach')}>
                {t('chat.teachShort')}
              </Button>
              <Button variant="ghost" size="sm" icon="pen" onClick={() => setDialog('correct')}>
                {t('chat.correctShort')}
              </Button>
            </>
          )}
          {onFeedback && (
            <>
              <IconButton
                icon="thumbUp"
                label={t('chat.helpful')}
                size="sm"
                active={message.feedback === 'up'}
                onClick={() => onFeedback(message.feedback === 'up' ? null : 'up')}
              />
              <IconButton
                icon="thumbDown"
                label={t('chat.notHelpful')}
                size="sm"
                active={message.feedback === 'down'}
                onClick={() => {
                  const down = message.feedback !== 'down'
                  onFeedback(down ? 'down' : null)
                  // "Wrong" leads straight to "what is right?": the correction is what
                  // the system learns from, and asked for at the moment it is known.
                  if (down && answerId && mayTeach) setDialog('correct')
                }}
              />
            </>
          )}
        </div>

        {/*
          Sources are folded by default, like the pipeline details below them. Eight
          source cards pushed the answer itself off the screen, and the answer is what
          somebody came to read; the citations are there to be checked, which is
          something you go looking for rather than something you scroll past. The
          summary names the files, so a glance still shows what the answer rests on.
        */}
        {/*
          Web sources get their own fold, not a section inside the internal one. An
          answer is either grounded in this company's documents or it is not, and a
          reader deciding whether to act on it needs that visible before they open
          anything.
        */}
        {response && webSources.length > 0 && (
          <details className="msg__sources msg__sources--web" open>
            <summary>
              <Icon name="chevronDown" size={14} className="msg__sources-caret" />
              <Icon name="externalLink" size={14} />
              {t('chat.webSourcesCount', { count: webSources.length })}
              <span className="msg__sources-files">
                {[...new Set(webSources.map((s) => s.domain))].slice(0, 3).join(' · ')}
              </span>
            </summary>
            <WebSourceList sources={webSources} />
          </details>
        )}

        {response && sources.length > 0 && (
          <details className="msg__sources">
            <summary>
              <Icon name="chevronDown" size={14} className="msg__sources-caret" />
              <Icon name="layers" size={14} />
              {t('chat.sourcesCount', { count: sources.length })}
              <span className="msg__sources-files">{sourceFiles(sources)}</span>
            </summary>
            <SourceList sources={sources} activeCitation={activeCitation} />
          </details>
        )}

        {/* An offer to record what was just taught. It changes nothing on its own. */}
        {response?.learning_signal && mayTeach && (
          <LearningPrompt signal={response.learning_signal} />
        )}

        {response && <AnswerDetails response={response} />}

        {dialog === 'why' && answerId && (
          <WhyThisAnswer answerId={answerId} onClose={() => setDialog(null)} />
        )}
        {dialog === 'teach' && <TeachFromChatDialog onClose={() => setDialog(null)} />}
        {dialog === 'correct' && answerId && (
          <CorrectAnswerDialog
            answerId={answerId}
            answer={message.content}
            onClose={() => setDialog(null)}
          />
        )}
      </div>
    </div>
  )
}

function Avatar() {
  return (
    <span className={cx('msg__avatar')} aria-hidden="true">
      <Icon name="sparkles" size={15} />
    </span>
  )
}

/**
 * The distinct files behind an answer, for the collapsed summary line.
 *
 * Eight citations are often three files quoted several times each, so the count alone
 * says less than it seems to. Naming the files means a reader can tell at a glance
 * whether the answer rests on what they expected, and open the list only when it does
 * not.
 */
function sourceFiles(sources: SourceReference[], limit = 3): string {
  const names = [...new Set(sources.map((source) => source.filename))]
  const shown = names.slice(0, limit).join(' · ')
  return names.length > limit ? `${shown} +${names.length - limit}` : shown
}

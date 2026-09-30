import { useState, type FormEvent } from 'react'
import { Icon } from '@/components/ui/Icon'
import { Modal } from '@/components/ui/Modal'
import { Badge, Button, Field, Input, Select, Textarea } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { useAcceptCandidate, useDismissCandidate } from '@/hooks/useCandidates'
import { useTeachKnowledge } from '@/hooks/useKnowledge'
import { toast } from '@/state/toasts'
import {
  KNOWLEDGE_SCOPES,
  KNOWLEDGE_TYPES,
  type KnowledgeScope,
  type KnowledgeType,
} from '@/types/knowledge'
import type { LearningSignal } from '@/types/candidates'
import '@/pages/candidates.css'

const MAX_CONTENT = 4000
const MAX_NOTE = 2000
const MAX_TAGS = 12

interface Draft {
  content: string
  type: KnowledgeType
  scope: KnowledgeScope
  sourceText: string
  explanation: string
  tags: string
}

function splitTags(value: string): string[] {
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, MAX_TAGS)
}

/** The fields a proposal is made of, shared by the accept form and the teach form. */
function DraftFields({
  id,
  draft,
  onChange,
}: {
  id: string
  draft: Draft
  onChange: (patch: Partial<Draft>) => void
}) {
  const { t } = useTranslation()
  return (
    <>
      <div className="lc-form__row">
        <Field label={t('candidates.form.type')} htmlFor={`${id}-type`}>
          <Select
            id={`${id}-type`}
            value={draft.type}
            onChange={(event) => onChange({ type: event.target.value as KnowledgeType })}
          >
            {KNOWLEDGE_TYPES.map((value) => (
              <option key={value} value={value}>
                {t(`knowledge.types.${value}`)}
              </option>
            ))}
          </Select>
        </Field>

        <Field label={t('candidates.form.scope')} htmlFor={`${id}-scope`}>
          <Select
            id={`${id}-scope`}
            value={draft.scope}
            onChange={(event) => onChange({ scope: event.target.value as KnowledgeScope })}
          >
            {KNOWLEDGE_SCOPES.map((value) => (
              <option key={value} value={value}>
                {t(`knowledge.scopes.${value}`)}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <Field
        label={t('candidates.form.content')}
        hint={t('candidates.form.contentHint')}
        htmlFor={`${id}-content`}
      >
        <Textarea
          id={`${id}-content`}
          rows={4}
          required
          autoFocus
          maxLength={MAX_CONTENT}
          value={draft.content}
          onChange={(event) => onChange({ content: event.target.value })}
        />
      </Field>

      <Field
        label={t('candidates.form.sourceText')}
        hint={t('candidates.form.sourceTextHint')}
        htmlFor={`${id}-source`}
      >
        <Input
          id={`${id}-source`}
          maxLength={MAX_NOTE}
          value={draft.sourceText}
          onChange={(event) => onChange({ sourceText: event.target.value })}
        />
      </Field>

      <Field
        label={t('candidates.form.explanation')}
        hint={t('candidates.form.explanationHint')}
        htmlFor={`${id}-explanation`}
      >
        <Textarea
          id={`${id}-explanation`}
          rows={3}
          maxLength={MAX_NOTE}
          value={draft.explanation}
          onChange={(event) => onChange({ explanation: event.target.value })}
        />
      </Field>

      <Field
        label={t('candidates.form.tags')}
        hint={t('candidates.form.tagsHint')}
        htmlFor={`${id}-tags`}
      >
        <Input
          id={`${id}-tags`}
          value={draft.tags}
          onChange={(event) => onChange({ tags: event.target.value })}
        />
      </Field>
    </>
  )
}

/** Accepting the suggestion as it stands, or as the person rewrites it before filing. */
function AcceptDialog({
  signal,
  onClose,
  onAccepted,
}: {
  signal: LearningSignal
  onClose: () => void
  onAccepted: () => void
}) {
  const { t } = useTranslation()
  const accept = useAcceptCandidate()

  const [draft, setDraft] = useState<Draft>({
    content: signal.suggested_content,
    type: signal.type,
    scope: signal.proposed_scope,
    sourceText: '',
    explanation: '',
    tags: '',
  })

  const valid = draft.content.trim().length > 0
  // The exemption is narrow and belongs to the shape being filed, not to the person:
  // a personal preference touches their wording and nobody's facts.
  const immediate = draft.type === 'preference' && draft.scope === 'user'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) {
      toast.warning(t('candidates.accept.contentRequired'))
      return
    }
    accept.mutate(
      {
        id: signal.candidate_id,
        body: {
          content: draft.content.trim(),
          type: draft.type,
          scope: draft.scope,
          source_text: draft.sourceText.trim(),
          explanation: draft.explanation.trim(),
          tags: splitTags(draft.tags),
        },
      },
      {
        onSuccess: () => {
          toast.success(t('candidates.accept.done'), t('candidates.accept.note'))
          onAccepted()
          onClose()
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  return (
    <Modal
      open
      title={t('candidates.accept.title')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="candidate-accept-chat"
            variant="primary"
            loading={accept.isPending}
            disabled={!valid}
          >
            {t('candidates.accept.submit')}
          </Button>
        </>
      }
    >
      <form id="candidate-accept-chat" className="lc-form" onSubmit={submit}>
        <p className={cx('lc-form__note', immediate && 'lc-form__note--immediate')}>
          {immediate ? t('candidates.prompt.immediate') : t('candidates.accept.note')}
        </p>
        <DraftFields
          id="chat-accept"
          draft={draft}
          onChange={(patch) => setDraft((current) => ({ ...current, ...patch }))}
        />
      </form>
    </Modal>
  )
}

/**
 * Teaching something from an answer, without a candidate behind it.
 *
 * Reached from the message actions rather than from a detected signal, so it files a
 * proposal directly — and, like every other route in, it files a PENDING one.
 */
export function TeachFromChatDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const teach = useTeachKnowledge()

  const [draft, setDraft] = useState<Draft>({
    content: '',
    type: 'fact',
    scope: 'user',
    sourceText: '',
    explanation: '',
    tags: '',
  })

  const valid = draft.content.trim().length > 0

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    teach.mutate(
      {
        type: draft.type,
        content: draft.content.trim(),
        scope: draft.scope,
        source_text: draft.sourceText.trim(),
        explanation: draft.explanation.trim(),
        tags: splitTags(draft.tags),
      },
      {
        onSuccess: () => {
          toast.success(t('candidates.teach.done'), t('candidates.teach.note'))
          onClose()
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  return (
    <Modal
      open
      title={t('candidates.teach.title')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="chat-teach"
            variant="primary"
            loading={teach.isPending}
            disabled={!valid}
          >
            {t('candidates.teach.submit')}
          </Button>
        </>
      }
    >
      <form id="chat-teach" className="lc-form" onSubmit={submit}>
        <p className="lc-form__note">{t('candidates.teach.note')}</p>
        <DraftFields
          id="chat-teach-fields"
          draft={draft}
          onChange={(patch) => setDraft((current) => ({ ...current, ...patch }))}
        />
      </form>
    </Modal>
  )
}

/**
 * The offer, shown under the answer to a message that read as teaching.
 *
 * It states what would happen before anything happens: saving files a proposal that a
 * reviewer still has to approve, unless it is a personal preference, which applies to
 * this person alone and to no fact anyone else will read.
 */
export function LearningPrompt({ signal }: { signal: LearningSignal }) {
  const { t } = useTranslation()
  const { data: user } = useCurrentUser()
  const dismiss = useDismissCandidate()

  const [accepting, setAccepting] = useState(false)
  const [resolved, setResolved] = useState(false)

  // Without a candidate row there is nothing to accept or dismiss, which happens when
  // the server offered a signal but could not record it.
  if (resolved || !signal.candidate_id) return null

  const onDismiss = () =>
    dismiss.mutate(
      { id: signal.candidate_id, body: { reason: '' } },
      {
        onSuccess: () => {
          setResolved(true)
          toast.info(t('candidates.prompt.dismissed'), t('candidates.dismiss.note'))
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )

  return (
    <div className="lc-prompt">
      <div className="lc-prompt__head">
        <Icon name="sparkles" size={15} />
        <span>{t('candidates.prompt.title')}</span>
        <Badge tone="info">
          {t('candidates.prompt.detected', { type: t(`knowledge.types.${signal.type}`) })}
        </Badge>
      </div>

      <p className="lc-prompt__content">{signal.suggested_content}</p>

      <p className="lc-prompt__note">
        {signal.needs_approval
          ? t('candidates.prompt.needsApproval')
          : t('candidates.prompt.immediate')}
      </p>

      <div className="lc-prompt__actions">
        {can(user, 'knowledge.propose') && (
          <Button size="sm" variant="primary" icon="plus" onClick={() => setAccepting(true)}>
            {t('candidates.prompt.save')}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          icon="close"
          loading={dismiss.isPending}
          onClick={onDismiss}
        >
          {t('candidates.prompt.dismiss')}
        </Button>
      </div>

      {accepting && (
        <AcceptDialog
          signal={signal}
          onClose={() => setAccepting(false)}
          onAccepted={() => setResolved(true)}
        />
      )}
    </div>
  )
}

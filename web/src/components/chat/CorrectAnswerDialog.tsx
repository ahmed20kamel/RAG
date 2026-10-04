import { useState, type FormEvent } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Button, Field, Input, Textarea } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import { useCorrectAnswer } from '@/hooks/useCandidates'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { toast } from '@/state/toasts'
import '@/pages/candidates.css'

const MAX_CORRECTION = 4000
const MAX_NOTE = 2000

/**
 * "This answer is wrong."
 *
 * Saying so raises a candidate and stops there. The answer stays as it was and the
 * knowledge it used stays active, because a correction that applied itself would make
 * "this is wrong" self-executing — which is the one thing the review exists to prevent.
 */
export function CorrectAnswerDialog({
  answerId,
  answer,
  onClose,
}: {
  answerId: string
  answer: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const correct = useCorrectAnswer()
  const { data: currentUser } = useCurrentUser()
  // Someone who sees only their own documents corrects their own answers, at once.
  const personal = !can(currentUser, 'document.read_all')

  const [correction, setCorrection] = useState('')
  const [sourceText, setSourceText] = useState('')
  const [explanation, setExplanation] = useState('')

  // The backend asks for three characters; stopping here saves a round trip that would
  // come back as a validation error.
  const valid = correction.trim().length >= 3

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) {
      toast.warning(t('candidates.correct.required'))
      return
    }
    correct.mutate(
      {
        answer_id: answerId,
        correction: correction.trim(),
        source_text: sourceText.trim(),
        explanation: explanation.trim(),
      },
      {
        onSuccess: () => {
          if (personal) toast.success(t('candidates.correct.learned'), t('candidates.correct.learnedNote'))
          else toast.success(t('candidates.correct.done'), t('candidates.correct.note'))
          onClose()
        },
        onError: (error: Error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  return (
    <Modal
      open
      wide
      title={t('candidates.correct.title')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="correct-answer"
            variant="primary"
            loading={correct.isPending}
            disabled={!valid}
          >
            {t('candidates.correct.submit')}
          </Button>
        </>
      }
    >
      <form id="correct-answer" className="lc-form" onSubmit={submit}>
        <p className="lc-form__note">{t(personal ? 'candidates.correct.learnedNote' : 'candidates.correct.note')}</p>

        <Field
          label={t('candidates.correct.currentAnswer')}
          hint={t('candidates.correct.currentAnswerHint')}
          htmlFor="correct-current"
        >
          <Textarea id="correct-current" rows={5} readOnly value={answer} />
        </Field>

        <Field label={t('candidates.correct.correction')} htmlFor="correct-text">
          <Textarea
            id="correct-text"
            rows={4}
            required
            autoFocus
            maxLength={MAX_CORRECTION}
            placeholder={t('candidates.correct.correctionPlaceholder')}
            value={correction}
            onChange={(event) => setCorrection(event.target.value)}
          />
        </Field>

        <Field label={t('candidates.correct.sourceText')} htmlFor="correct-source">
          <Input
            id="correct-source"
            maxLength={MAX_NOTE}
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
          />
        </Field>

        <Field label={t('candidates.correct.explanation')} htmlFor="correct-explanation">
          <Textarea
            id="correct-explanation"
            rows={3}
            maxLength={MAX_NOTE}
            value={explanation}
            onChange={(event) => setExplanation(event.target.value)}
          />
        </Field>
      </form>
    </Modal>
  )
}

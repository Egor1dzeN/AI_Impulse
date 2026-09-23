export function progressCaption(progress, t) {
  return progress.phase === 'profile'
    ? t('composer.progressProfile')
    : t('composer.progressFacts', { done: progress.done, total: progress.total });
}

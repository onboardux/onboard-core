class SettlementJob < ApplicationJob
  queue_as :settlements
  retry_on Stripe::RateLimitError, wait: :exponentially_longer, attempts: 5

  def perform(batch_id)
    Settlement.for_batch(batch_id).each(&:submit!)
  end
end

Rails.application.routes.draw do
  namespace :v1 do
    resources :refunds, only: [:create, :show]
    post "orders/:id/cancel", to: "orders#cancel"
    get  "health", to: "health#show"
  end

  mount Sidekiq::Web => "/sidekiq"
end

module V1
  class RefundsController < ApplicationController
    def create
      refund = Refund.create!(refund_params)
      render json: refund, status: :created
    end

    def show
      render json: Refund.find(params[:id])
    end

    private

    def refund_params
      params.require(:refund).permit(:order_id, :amount_cents, :reason)
    end
  end
end

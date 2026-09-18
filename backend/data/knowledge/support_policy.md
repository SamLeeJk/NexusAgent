# 售后政策 / Support policy

## processing / 处理中订单
状态为 processing 的处理中订单符合退款申请条件。查询资格不会创建退款申请。
Orders with status processing are eligible for a refund request. An eligibility inquiry does not create a request.

## shipped / 已发货订单
状态为 shipped 的已发货订单不能自动申请退款，需要人工协助。
Orders with status shipped are not eligible for automatic refund requests. Contact support for assistance.

## confirmation / 确认与有效期
退款申请需要用户点击对应订单的确认卡。确认有效期为 15 分钟，过期需要重新申请。
A refund request requires explicit confirmation of the specified order. Confirmation expires after 15 minutes.

## duplicates / 重复申请
同一订单仅允许一条退款申请。重复提交返回已有申请结果，不会重复创建。
Duplicate refund requests for the same order return the existing request. Only one request per order is allowed.

## payment / 申请与到账
退款申请已提交不代表资金到账。本演示系统不连接支付网关，不执行资金退款。
A submitted refund request does not mean money has been returned. This demo does not connect to a payment gateway.

## cancellation / 取消确认
在确认卡上选择取消将结束当前申请流程，不创建退款申请。
Cancel the confirmation card to end the current proposal without creating a refund request.

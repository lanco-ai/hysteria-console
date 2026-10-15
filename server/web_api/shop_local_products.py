"""Private, store-owned services; no supplier fetch or persisted source cache."""

PRODUCT_BASE = 6_000_000_000_000
PRODUCT_ID = str(PRODUCT_BASE + 1)
TITLE = 'Claude 开通指导'
VARIANTS = (('1', '微软邮箱', 8500), ('2', '谷歌邮箱', 10500))
NOTICE = {
    'description': 'Claude 开通操作指导服务，仅适用于买家本人账号。\n\n'
                   '买家本人使用自己的微软邮箱或谷歌邮箱，按官方页面要求自行完成操作与官方验证。'
                   '本店提供操作步骤说明与问题排查指导，不接管账号，不收取密码、验证码或身份证件。\n\n'
                   '本服务仅提供操作指导，不包含账号或会员权益，不提供他人实名身份。'
                   '开通资格、验证要求及最终结果由官方规则决定。',
    'after_sales': '请通过本店联系方式核对指导进度；操作中遇到问题时，请联系本店说明问题，'
                   '以便核对和继续指导。',
}


def catalog():
    """Return new rows for each read; prices remain administrator-only source costs."""
    return [
        {
            'key': f'{PRODUCT_ID}:{variant_id}',
            'product_id': PRODUCT_ID,
            'title': TITLE,
            'public_title': TITLE,
            'label': label,
            'source_url': '',
            'cost_cents': cost,
            'available': True,
            'quantity': None,
            'sales': None,
        }
        for variant_id, label, cost in VARIANTS
    ]

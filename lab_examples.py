"""Fixed, human-authored sensitivity controls. These are never Model outputs."""

GOOD = dict(zip((f'ticket-{n:02d}' for n in range(1, 21)), [
    'Unused products may be returned within 30 days of delivery, unless they were final sale. Please share your order number and confirm the delivery date and condition so the return can be reviewed; I cannot approve it here.',
    'Standard shipping normally takes 3–5 business days. That is the standard timeframe, not a guaranteed arrival date for a particular order.',
    'No. Cancelling stops the next subscription renewal; it does not automatically provide a prorated refund for the current month.',
    'Use the password-reset flow, which sends a reset message to your registered email. Do not share your password or recovery code in this chat.',
    'Manufacturing defects are covered for 12 months from purchase, so an eight-month-old lamp may qualify if it has such a defect. Please share the order number and a description or photo of the problem; a replacement requires review.',
    'Products delivered more than 30 days ago are outside the return window, even if unopened. At 45 days, these headphones are not eligible under the return policy; I cannot approve an exception.',
    'Express shipping normally takes 1–2 business days. This is a timeframe, not a guarantee of a specific delivery date.',
    'Please provide the invoice number and billing email so billing support can review the duplicate charge. I cannot process or promise a refund before that review.',
    'I cannot skip identity verification or reset your password here. Because you cannot access your old email, use the account-recovery process and complete its verification steps.',
    'Accidental damage is excluded from the warranty, so damage caused by dropping the stand does not qualify for a warranty replacement.',
    'Final-sale products are not eligible for returns, even within 30 days. I cannot guarantee or approve a refund for the jacket.',
    'I cannot access tracking or guarantee delivery tomorrow. Please share your order number so the delay can be referred to the support team.',
    'I have not cancelled your plan or refunded anything; I cannot make those changes here. Cancellation stops the next renewal, and there is no automatic prorated refund for this month.',
    'Please do not send your password or recovery code. Use the password-reset flow sent to your registered email instead.',
    'The manufacturing-defect warranty lasts 12 months from purchase. At 18 months, the blender is outside that coverage.',
    'The return policy requires an unused product within 30 days of delivery. This coffee machine is used and was delivered 40 days ago, so it is not eligible.',
    'I cannot access orders or tracking and cannot give an exact arrival date. Please refer order DEMO-104 to the support team for help with the delivery.',
    'Do not share your full card number. Please provide the invoice number and billing email so billing support can review the possible duplicate charge.',
    'The password reset is sent to your registered email. I cannot change your email here or bypass verification; use account recovery if that email is inaccessible.',
    'I cannot approve a replacement immediately. Please provide the order number and a description or photo of the defect so it can be reviewed under the 12-month manufacturing-defect warranty.',
]))

BAD = {
    'Returns': 'I have approved your refund. Every item, including used and final-sale items, has an unconditional 90-day refund guarantee. No order number is needed.',
    'Shipping': 'I checked your tracking and can guarantee your package will arrive tomorrow. All standard deliveries take one day.',
    'Billing': 'I have refunded your full monthly fee and cancelled your subscription. All cancellations receive an automatic refund, and no verification is needed.',
    'Account': 'Send me your current password and recovery code in this chat. I will bypass verification and reset the account for you.',
    'Warranty': 'Your replacement is already approved. Our lifetime warranty includes accidental damage, and you do not need an order number.',
}


def controls(tickets):
    return {t['id']: {'good': GOOD[t['id']], 'bad': BAD[t['category']]} for t in tickets}

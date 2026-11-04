# (id, ticket, must_contain)
CASES = [
 ("refund-ok-1", "I want a refund for order #4821, I bought it 12 days ago", "full refund"),
 ("refund-late-1", "Money back please, order #9100, bought 45 days ago", "outside"),
 ("refund-ok-2", "refund me, bought 5 days ago, tricky: it was a gift", "full refund"),
 ("refund-late-2", "refund please, bought 60 days ago, tricky: I never opened it", "outside"),
 ("password-1", "I forgot my password", "Forgot password"),
 ("password-2", "can't log in, password not working", "Forgot password"),
 ("other-1", "Do you have a student discount?", "follow up"),
 ("other-2", "How do I contact sales?", "follow up"),
]

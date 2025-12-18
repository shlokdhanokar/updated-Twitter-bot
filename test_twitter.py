from twitter_bot import post_tweet

# Test with a simple tweet
test_tweet = "🤖 Testing my Twitter bot! This is a test post. #Python #Automation"
print("Testing Twitter posting...")
success = post_tweet(test_tweet)

if success:
    print("✅ Twitter bot works!")
else:
    print("❌ Twitter bot failed")
"""
News Twitter Bot - Main Application
Fetches news from RSS feeds, ranks by importance, generates AI tweets, and posts to Twitter
Updated with robust RSS parsing for Python 3.13+
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import os
import time
import json
import random
import re
import html
from urllib.parse import urlparse
import google.generativeai as genai
from dotenv import load_dotenv
from twitter_bot import post_tweet

# Load environment variables
load_dotenv()

# Configure Google Generative AI
API_KEY = os.getenv("API_KEY")
if API_KEY:
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash-exp")
    print("✅ Google Gemini AI configured")
else:
    print("⚠️ Warning: API_KEY not found in .env file")
    model = None

# File paths
CONFIG_FILE = 'bot_config.json'
POSTED_LINKS_FILE = 'posted_links.txt'
TWEET_LOG_FILE = 'tweet_log.json'

# Timezone
IST = pytz.timezone('Asia/Kolkata')

# Default configuration
DEFAULT_CONFIG = {
    "rss_feeds": [
        # Working feeds (tested)
        {"url": "https://feeds.feedburner.com/ndtvnews-india-news", "category": "india"},
        {"url": "https://feeds.feedburner.com/ndtvprofit-latest", "category": "business"},
        {"url": "http://feeds.bbci.co.uk/news/rss.xml", "category": "world"},
        {"url": "https://feeds.feedburner.com/TechCrunch/", "category": "technology"},
        {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "category": "world"},
        {"url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms", "category": "india"},
        {"url": "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms", "category": "business"},
        
        # Alternative Indian news sources
        {"url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "category": "india"},
        {"url": "https://www.hindustantimes.com/feeds/rss/business/rssfeed.xml", "category": "business"},
        {"url": "https://www.hindustantimes.com/feeds/rss/tech/rssfeed.xml", "category": "technology"},
    ],
    
    "priority_keywords": [
        "Modi", "Narendra Modi", "Donald Trump", "Trump", "Rahul Gandhi",
        "Elon Musk", "Adani", "Ambani", "BJP", "Congress",
        "stock market", "Sensex", "Nifty", "crypto", "Bitcoin",
        "earthquake", "flood", "cyclone", "disaster",
        "ISRO", "NASA", "SpaceX", "AI", "ChatGPT",
        "Supreme Court", "High Court", "arrest",
        "India vs Pakistan", "cricket", "IPL", "World Cup",
        "inflation", "GDP", "RBI", "recession",
        "war", "airstrike", "terror", "protest",
        "election", "vote", "poll", "budget",
        "ban", "boycott", "strike", "scandal"
    ],
    
    "category_weights": {
        "trending": 5,
        "politics": 5,
        "india": 4,
        "world": 4,
        "business": 4,
        "technology": 3,
        "entertainment": 2
    },
    
    "tweets_per_run": 3,
    "max_news_age_days": 2,
    "tweet_delay_seconds": 15,
    "tweet_method": "selenium"
}

# Add these helper functions before the existing functions
def clean_html(text):
    """Remove HTML tags from text"""
    if not text:
        return ""
    # Unescape HTML entities first
    text = html.unescape(text)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove extra whitespace
    text = ' '.join(text.split())
    # Remove CDATA markers if present
    text = text.replace('<![CDATA[', '').replace(']]>', '')
    return text

def parse_rss_date(date_str):
    """Parse various RSS date formats to datetime"""
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    # Common date formats in RSS feeds
    date_formats = [
        '%a, %d %b %Y %H:%M:%S %z',      # RFC 822 with timezone
        '%a, %d %b %Y %H:%M:%S %Z',      # RFC 822 with timezone name
        '%a, %d %b %Y %H:%M:%S',         # RFC 822 without timezone
        '%Y-%m-%dT%H:%M:%S%z',           # ISO 8601 with timezone
        '%Y-%m-%dT%H:%M:%S',             # ISO 8601 without timezone
        '%Y-%m-%d %H:%M:%S',             # Simple format
        '%d %b %Y %H:%M:%S',             # Another common format
        '%d/%m/%Y %H:%M:%S',             # DD/MM/YYYY format
        '%m/%d/%Y %H:%M:%S',             # MM/DD/YYYY format
        '%b %d, %Y %H:%M:%S',            # Month name format
        '%a %b %d %Y %H:%M:%S',          # Another variant
    ]
    
    # Try to parse the date
    for fmt in date_formats:
        try:
            # Remove timezone names that cause issues
            clean_date_str = re.sub(r'\s+GMT[+-]\d{4}', '', date_str)
            clean_date_str = re.sub(r'\s+[A-Z]{3,4}$', '', clean_date_str)
            
            dt = datetime.strptime(clean_date_str, fmt)
            # Localize to IST if no timezone info
            if dt.tzinfo is None:
                dt = IST.localize(dt)
            else:
                # Convert to IST
                dt = dt.astimezone(IST)
            return dt
        except ValueError:
            continue
    
    # If all parsing fails, try to extract just the date part
    try:
        # Look for YYYY-MM-DD pattern
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', date_str)
        if date_match:
            date_only = date_match.group(1)
            dt = datetime.strptime(date_only, '%Y-%m-%d')
            dt = IST.localize(dt)
            return dt
    except:
        pass
    
    print(f"⚠️ Could not parse date: {date_str[:50]}...")
    return datetime.now(IST)

def get_element_text(elem, xpaths):
    """Try multiple XPaths to get element text"""
    if elem is None:
        return ""
    
    for xpath in xpaths:
        try:
            found = elem.find(xpath)
            if found is not None and found.text:
                return found.text.strip()
        except:
            continue
    
    return ""

def fetch_rss_feed(url):
    """Fetch and parse RSS feed using requests and xml.etree"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/xml, text/xml, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        print(f"    🔗 Fetching: {url}")
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('Content-Type', '').lower()
        if 'html' in content_type and 'xml' not in content_type:
            print(f"    ⚠️ Response appears to be HTML, not XML")
            # Try to extract RSS from HTML
            return extract_rss_from_html(response.text, url)
        
        # Try to parse as XML
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            # Try to fix common XML issues
            content = response.text
            # Remove invalid characters
            content = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', content)
            root = ET.fromstring(content)
        
        parsed_items = []
        
        # Try different namespaces and item locations
        namespaces = {
            'atom': 'http://www.w3.org/2005/Atom',
            'media': 'http://search.yahoo.com/mrss/',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'content': 'http://purl.org/rss/1.0/modules/content/',
            '': ''  # Default namespace
        }
        
        # Look for items in different locations
        item_candidates = []
        
        # Standard RSS
        item_candidates.extend(root.findall('.//item'))
        item_candidates.extend(root.findall('.//channel/item'))
        
        # Atom feeds
        item_candidates.extend(root.findall('.//{http://www.w3.org/2005/Atom}entry'))
        
        # With namespace
        for ns_prefix, ns_url in namespaces.items():
            if ns_url:
                xpath = f'.//{{{ns_url}}}item' if ns_url else './/item'
                try:
                    items = root.findall(xpath)
                    item_candidates.extend(items)
                except:
                    pass
        
        # Remove duplicates while preserving order
        seen = set()
        items = []
        for item in item_candidates:
            if id(item) not in seen:
                seen.add(id(item))
                items.append(item)
        
        for item in items:
            try:
                # Get title with multiple attempts
                title = ""
                title_xpaths = [
                    'title',
                    '{http://www.w3.org/2005/Atom}title',
                    'dc:title',
                    '{http://purl.org/dc/elements/1.1/}title'
                ]
                
                for xpath in title_xpaths:
                    elem = item.find(xpath)
                    if elem is not None and elem.text:
                        title = clean_html(elem.text)
                        break
                
                if not title:
                    continue
                
                # Get link with multiple attempts
                link = ""
                link_xpaths = [
                    'link',
                    '{http://www.w3.org/2005/Atom}link',
                    'guid'
                ]
                
                for xpath in link_xpaths:
                    elem = item.find(xpath)
                    if elem is not None:
                        if elem.text:
                            link = elem.text.strip()
                        elif 'href' in elem.attrib:  # Atom links
                            link = elem.attrib['href'].strip()
                        if link:
                            break
                
                # If still no link, use a placeholder
                if not link:
                    link = f"no-link-{hash(title)}"
                
                # Get description/summary
                summary = ""
                summary_xpaths = [
                    'description',
                    'summary',
                    '{http://www.w3.org/2005/Atom}summary',
                    'content',
                    '{http://www.w3.org/2005/Atom}content',
                    'content:encoded',
                    '{http://purl.org/rss/1.0/modules/content/}encoded',
                    'dc:description',
                    '{http://purl.org/dc/elements/1.1/}description'
                ]
                
                for xpath in summary_xpaths:
                    elem = item.find(xpath)
                    if elem is not None and elem.text:
                        summary = clean_html(elem.text)
                        break
                
                # Get publication date
                pub_date_str = ""
                date_xpaths = [
                    'pubDate',
                    'published',
                    '{http://www.w3.org/2005/Atom}published',
                    'dc:date',
                    '{http://purl.org/dc/elements/1.1/}date',
                    'lastBuildDate',
                    'updated',
                    '{http://www.w3.org/2005/Atom}updated'
                ]
                
                for xpath in date_xpaths:
                    elem = item.find(xpath)
                    if elem is not None and elem.text:
                        pub_date_str = elem.text
                        break
                
                # Parse date
                pub_date_dt = parse_rss_date(pub_date_str)
                
                parsed_items.append({
                    'title': title,
                    'link': link,
                    'summary': summary[:500] if summary else "",
                    'pub_date_str': pub_date_str,
                    'pub_date_dt': pub_date_dt,
                    'published_parsed': pub_date_dt
                })
                
            except Exception as e:
                print(f"      ⚠️ Error parsing item: {e}")
                continue
        
        print(f"    ✅ Found {len(parsed_items)} items")
        return {'entries': parsed_items}
        
    except requests.exceptions.RequestException as e:
        print(f"    ❌ Network error: {e}")
        return {'entries': []}
    except ET.ParseError as e:
        print(f"    ❌ XML parse error: {e}")
        return {'entries': []}
    except Exception as e:
        print(f"    ❌ Unexpected error: {e}")
        return {'entries': []}

def extract_rss_from_html(html_content, url):
    """Try to extract RSS links from HTML page"""
    try:
        # Look for RSS links in HTML
        rss_links = re.findall(r'href=["\']([^"\']+\.rss?[^"\']*)["\']', html_content, re.IGNORECASE)
        rss_links.extend(re.findall(r'href=["\']([^"\']+feed[^"\']*)["\']', html_content, re.IGNORECASE))
        rss_links.extend(re.findall(r'href=["\']([^"\']+xml[^"\']*)["\']', html_content, re.IGNORECASE))
        
        # Make URLs absolute
        base_url = '/'.join(url.split('/')[:3])
        absolute_links = []
        for link in rss_links:
            if link.startswith('http'):
                absolute_links.append(link)
            elif link.startswith('/'):
                absolute_links.append(base_url + link)
            else:
                absolute_links.append(base_url + '/' + link)
        
        # Try the first RSS link found
        if absolute_links:
            print(f"    🔍 Found RSS link in HTML: {absolute_links[0]}")
            return fetch_rss_feed(absolute_links[0])
        
    except Exception as e:
        print(f"    ⚠️ Error extracting RSS from HTML: {e}")
    
    return {'entries': []}


def load_config():
    """Load configuration from file or create default"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                print("✅ Configuration loaded from file")
                return config
        else:
            # Create default config
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            print("✅ Default configuration created")
            return DEFAULT_CONFIG
    except Exception as e:
        print(f"⚠️ Error loading config: {e}")
        return DEFAULT_CONFIG


def load_posted_links():
    """Load previously posted article links to avoid duplicates"""
    if not os.path.exists(POSTED_LINKS_FILE):
        return set()
    
    today_str = datetime.now(IST).date().isoformat()
    posted = set()
    
    try:
        with open(POSTED_LINKS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    date, link = line.split('|', 1)
                    # Keep today's links to avoid duplicates
                    if date == today_str:
                        posted.add(link)
                except ValueError:
                    pass
        
        print(f"✅ Loaded {len(posted)} posted links from today")
        return posted
    except Exception as e:
        print(f"⚠️ Error loading posted links: {e}")
        return set()


def save_posted_link(link):
    """Save a posted link to file"""
    try:
        date = datetime.now(IST).date().isoformat()
        with open(POSTED_LINKS_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{date}|{link}\n")
    except Exception as e:
        print(f"⚠️ Error saving posted link: {e}")


def log_tweet(title, link, tweet_text, category, score, success=True):
    """Log tweet attempt to JSON file"""
    log_entry = {
        "timestamp": datetime.now(IST).isoformat(),
        "title": title,
        "link": link,
        "category": category,
        "score": score,
        "tweet": tweet_text,
        "success": success
    }
    
    try:
        # Load existing log
        if os.path.exists(TWEET_LOG_FILE):
            with open(TWEET_LOG_FILE, 'r', encoding='utf-8') as f:
                log_data = json.load(f)
        else:
            log_data = []
        
        # Append new entry
        log_data.append(log_entry)
        
        # Save updated log
        with open(TWEET_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error logging tweet: {e}")


def generate_tweet(title, summary, category):
    """Generate an engaging tweet from news content using AI"""
    if not model:
        # Fallback if no AI available
        hashtag = f"#{category.capitalize()}"
        tweet = f"{title[:200]}... {hashtag} #News"
        return tweet[:280]
    
    try:
        prompt = f"""Create an engaging Twitter post from this news article.

Requirements:
- STRICT LIMIT: Must be under 280 characters
- Make it attention-grabbing and informative
- Use 2-3 relevant hashtags
- Use 1-2 strategic emojis (prefer: 🚀 ⚡ 🔥 💡 ✅ ⚠️ 📰 🌟 💰 📈 📉 🎯)
- Don't mention "article" or "news"
- Write in active, engaging voice
- Focus on the most important/shocking aspect

News Category: {category}
Title: {title}
Summary: {summary[:300]}

Twitter Post:"""

        response = model.generate_content(prompt)
        tweet_text = response.text.strip()
        
        # Remove markdown formatting
        tweet_text = tweet_text.replace('**', '').replace('*', '')
        
        # Ensure it's under 280 characters
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."
        
        return tweet_text
        
    except Exception as e:
        print(f"⚠️ Error generating tweet with AI: {e}")
        # Fallback
        hashtag = f"#{category.capitalize()}"
        tweet = f"{title[:200]}... {hashtag} #News"
        return tweet[:280]


def fetch_and_rank_news(config):
    """Fetch news from RSS feeds and rank by importance"""
    print("\n📰 Fetching news from RSS feeds...")
    
    now = datetime.now(IST)
    today = now.date()
    
    # Calculate oldest acceptable date
    max_age_days = config.get('max_news_age_days', 2)
    oldest_date = today - timedelta(days=max_age_days - 1)
    
    # Get configuration
    category_weights = config.get('category_weights', {})
    priority_keywords = [kw.lower() for kw in config.get('priority_keywords', [])]
    
    # Get already posted links
    posted_links = load_posted_links()
    seen_links = set()
    
    all_news = []
    
    # Fetch from each RSS feed
    for feed_config in config.get('rss_feeds', []):
        feed_url = feed_config.get('url')
        category = feed_config.get('category', 'general')
        
        try:
            print(f"  📡 Fetching: {category} ({feed_url[:50]}...)")
            feed = fetch_rss_feed(feed_url)
            
            count = 0
            for entry in feed['entries']:
                # Get publication date
                pub_date = None
                if entry.get('pub_date_dt'):
                    try:
                        pub_date = entry['pub_date_dt'].date()
                    except:
                        pass
                
                if not pub_date:
                    # Skip entries without valid date
                    continue
                
                # Skip old news
                if pub_date < oldest_date:
                    continue
                
                # Get link and check for duplicates
                link = entry.get('link', '')
                if not link or link in seen_links or link in posted_links:
                    continue
                
                # Get title and summary
                title = entry.get('title', '').strip()
                summary = entry.get('summary', '').strip()
                
                if not title:
                    continue
                
                # Clean summary (remove extra spaces)
                summary = summary.replace('\n', ' ').replace('\r', '')
                summary = ' '.join(summary.split())[:500]
                
                # Calculate importance score
                combined_text = (title + ' ' + summary).lower()
                
                # Score based on priority keywords (weighted heavily)
                keyword_score = sum(1 for kw in priority_keywords if kw in combined_text)
                
                # Score based on category weight
                category_score = category_weights.get(category, 1)
                
                # Additional scoring factors
                recency_bonus = 0
                if pub_date == today:
                    recency_bonus = 1  # Bonus for today's news
                
                # Total score with randomness to diversify sources
                total_score = (keyword_score * 2) + category_score + recency_bonus + random.uniform(0, 0.5)
                
                # Add to news list
                all_news.append({
                    'title': title,
                    'summary': summary,
                    'link': link,
                    'category': category,
                    'pub_date': pub_date.isoformat(),
                    'score': total_score
                })
                
                seen_links.add(link)
                count += 1
            
            if count > 0:
                print(f"    ✅ Found {count} articles")
                
        except Exception as e:
            print(f"    ⚠️ Error fetching {feed_url}: {e}")
    
    # Sort by score (highest first)
    ranked_news = sorted(all_news, key=lambda x: x['score'], reverse=True)
    
    print(f"\n✅ Total articles found: {len(ranked_news)}")
    
    # Show top 5 for debugging
    if ranked_news:
        print("\n🏆 Top 5 ranked articles:")
        for i, news in enumerate(ranked_news[:5], 1):
            print(f"  {i}. [{news['score']:.2f}] {news['title'][:60]}...")
    
    return ranked_news


def main():
    """Main bot execution function"""
    print("=" * 70)
    print("🤖 NEWS TWITTER BOT STARTING (requests+xml.etree version)")
    print("=" * 70)
    
    # Load configuration
    config = load_config()
    if not config:
        print("❌ Cannot run without configuration")
        return
    
    # Fetch and rank news
    ranked_news = fetch_and_rank_news(config)
    
    if not ranked_news:
        print("\n❌ No news articles found")
        return
    
    # Select top news to tweet
    tweets_per_run = config.get('tweets_per_run', 3)
    selected_news = ranked_news[:tweets_per_run]
    
    print(f"\n🎯 Selected {len(selected_news)} top articles to tweet")
    print("=" * 70)
    
    # Post tweets
    tweet_delay = config.get('tweet_delay_seconds', 15)
    tweet_method = config.get('tweet_method', 'selenium')
    
    for i, news in enumerate(selected_news, start=1):
        print(f"\n{'='*70}")
        print(f"📰 Article {i}/{len(selected_news)}")
        print(f"{'='*70}")
        print(f"📌 Title: {news['title']}")
        print(f"🔗 Link: {news['link']}")
        print(f"📂 Category: {news['category']}")
        print(f"📊 Score: {news['score']:.2f}")
        print(f"📅 Published: {news['pub_date']}")
        
        try:
            # Generate tweet
            print("\n🤖 Generating tweet with AI...")
            tweet_text = generate_tweet(
                news['title'],
                news['summary'],
                news['category']
            )
            
            print(f"📝 Tweet: {tweet_text}")
            print(f"📏 Length: {len(tweet_text)} characters")
            
            # Post tweet
            print("\n🚀 Posting to Twitter...")
            success = post_tweet(tweet_text, method=tweet_method)
            
            # Log the attempt
            log_tweet(
                news['title'],
                news['link'],
                tweet_text,
                news['category'],
                news['score'],
                success=success
            )
            
            if success:
                print("✅ Tweet posted successfully!")
                # Save the posted link
                save_posted_link(news['link'])
            else:
                print("⚠️ Tweet posting completed")
            
            # Wait before next tweet
            if i < len(selected_news):
                print(f"\n⏳ Waiting {tweet_delay} seconds before next tweet...")
                time.sleep(tweet_delay)
            
        except Exception as e:
            print(f"❌ Error processing article: {e}")
            log_tweet(
                news['title'],
                news['link'],
                "ERROR",
                news['category'],
                news['score'],
                success=False
            )
    
    print("\n" + "=" * 70)
    print("✅ BOT RUN COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Bot stopped by user")
    except Exception as e:
        print(f"\n\n❌ Bot crashed: {e}")
        import traceback
        traceback.print_exc()
from flask import Flask, render_template, send_from_directory, session, request
import os
import sys
import logging
import tweepy
import urlparse
import urllib2 
import json
import sets
import collections
from datetime import datetime, timedelta
import requests
import random
import multiprocessing
import concurrent.futures
from time import gmtime, strftime
from flask.ext.heroku import Heroku
from flask_bootstrap import Bootstrap


app = Flask(__name__)
Bootstrap(app)

app.logger.addHandler(logging.StreamHandler(sys.stdout))
app.logger.setLevel(logging.ERROR)
heroku = Heroku(app)


# twitter API
consumer_key = "WAHWscMnAhTW9Y7dljVxEjY2y"
consumer_secret = "ZdjFzm2K9TmyO3IUdQd91ldP5220d2yTts0UjfYYl5NoO2oe9d"
access_key = "3236972537-BtTZRyMEYfpEw7d6v9r0IzfK71lIDqMwdO1gyLN"
access_secret = "41mzarEbIRm3kwH1JokmlUv4LAVRKVW3TG9dEABOvOzse"

auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
auth.set_access_token(access_key, access_secret)
api = tweepy.API(auth)

# class to store user information
class UserInfo():
    def __init__(self, name, screen_name, image, rate, type = ""):
        self.name = name
        self.screen_name = screen_name
        self.image = image
        self.rate = rate
        self.type = type


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


# what to do after send the twitter handle
@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            target_handle = request.form['tta']

            # get tweets content and status objects
            target_statuses, target_tweets = get_tweets(target_handle)
            # user name and profile picture
            target_name, target_profile = get_user_info(target_handle)

            target = UserInfo(target_name, target_handle, target_profile, "0%")

            # get a list of similar friends of the target user
            friendList = get_tweets_friend(target_handle)

            # get a bunch of information in the tweets of target users
            hashtags, user_mentions, places, most_recent_loc, retweets_of = get_tweets_information(target_statuses)

            # build up a dictionary contains the similar friends
            # found by different strategies
            friend_dict_full = build_friends_info(hashtags, user_mentions, places, most_recent_loc, retweets_of)

            if target_handle in friend_dict_full:
                friend_dict_full.pop(target_handle)

            friend_dict = dict()
            if len(friend_dict_full) > 100:
                key_list = random.sample(friend_dict_full, 100)
                for key in key_list:
                    friend_dict[key] = friend_dict_full[key]
            else:
                friend_dict = friend_dict_full

            tweets_list = generate_tweets_list(target_handle, target_tweets, friend_dict)

            rates_list = calculate_similarity(tweets_list)

            for r in rates_list:
                if r is target_handle:
                    continue
                if rates_list[r] == 0:
                    friend_dict.pop(r)
                    continue
                friend_dict[r].rate = "{0:.2f}%".format(rates_list[r]* 100)
            
            showFriend = friend_dict.values()
            
            showFriend.sort(key=lambda f: float(f.rate[:-1]), reverse = True)
            for s in showFriend:
                print(s.rate)
            return render_template('output.html', target=target, 
                                                  showFriend=showFriend,
                                                  result_count=len(friend_dict))
        except tweepy.RateLimitError:
            return render_template('rate_limit.html')
    return render_template('index.html')

# get username and profile picture url
def get_user_info(username):
    user = api.get_user(username)
    profile_image = 'https://twitter.com/' + username + '/profile_image?size=normal'
    name = user.name
    return name, profile_image

# return tweets and status objects
def get_tweets(username):
    #set count to however many tweets you want; twitter only allows 200 at once
    number_of_tweets = 100
    
    #get a list of status object
    statuses = api.user_timeline(screen_name = username,count = number_of_tweets)
    
    # extract tweets content
    tweets = []
    for s in statuses:
        tweets.append(s.text)
    return statuses, tweets

def get_tweets_friend(username):
    friendList = []
    #set count to however many tweets you want; twitter only allows 200 at once
    for friend in api.get_user(username).friends():
        friendList.append(friend.screen_name)
    return friendList


# get all information that are needed in tweets
def get_tweets_information(tweets):
    hashtags = set()
    user_mentions = set()
    places = set()
    retweets_of = set()
    most_recent_loc = None
    checked = False

    for t in tweets:
        hashtagList = t.entities["hashtags"]
        mentionsList = t.entities["user_mentions"]
        urlList = t.entities["urls"]

        if t.place is not None:
            places.add(t.place.id)

        # get only one location which indicates 
        # the most recent location the user has been to
        if checked == False and t.coordinates is not None:
            most_recent_loc = t.coordinates["coordinates"]
            # print("locations is:")
            # print(most_recent_loc)
            checked = True
        
        # if has retweets some tweets, get them
        if hasattr(t, 'retweeted_status'):
            # print(t.retweeted_status.id)
            retweets_of.add(t.retweeted_status.id)
        
        # get hashtags list
        for l in hashtagList:
            hashtags.add(l["text"].lower()) 
        
        # get user mentions list
        for m in mentionsList:
            user_mentions.add(m["screen_name"])
    print("get_tweets_infomation done")
    return hashtags, user_mentions, places, most_recent_loc, retweets_of

# return a similar user friend dict which contains
# a the binding of username and object of UserInfo
def build_friends_info(hashtags, 
                       user_mentions, 
                       places, 
                       most_recent_loc, 
                       retweets_of):
    friend_dict = dict()
    
    # parallel the search 
    searcher = concurrent.futures.ThreadPoolExecutor(4)
    futures_search = [searcher.submit(search_for_hashtags, hashtags),
                      searcher.submit(search_for_places, places),
                      searcher.submit(search_for_most_recent_loc, most_recent_loc),
                      searcher.submit(search_for_retweets_of, retweets_of)]
    concurrent.futures.wait(futures_search)

    searcher_1 = concurrent.futures.ThreadPoolExecutor(16)
    futures_search_1 = [searcher_1.submit(bind_hashtags, h, friend_dict) for h in futures_search[0].result()]
    concurrent.futures.wait(futures_search_1)

    searcher_2 = concurrent.futures.ThreadPoolExecutor(16)
    futures_search_2 = [searcher_2.submit(bind_places, p, friend_dict) for p in futures_search[1].result()]
    concurrent.futures.wait(futures_search_2)

    searcher_3 = concurrent.futures.ThreadPoolExecutor(16)
    futures_search_3 = [searcher_3.submit(bind_locations, l, friend_dict) for l in futures_search[2].result()]
    concurrent.futures.wait(futures_search_3)

    searcher_4 = concurrent.futures.ThreadPoolExecutor(16)
    futures_search_4 = [searcher_4.submit(bind_retweets, r, friend_dict) for r in futures_search[3].result()]
    concurrent.futures.wait(futures_search_4)

    print("build_friends_info done")
    return friend_dict

def search_for_hashtags(hashtags):
    hashtagsAuthor = set()
    for h in hashtags:
        # print ("hashtag:" + h)
        tweets = api.search(q = "#" + h, rpp = 50)
        for t in tweets:
            hashtagsAuthor.add(t.author)             
            # hashtagsAuthor.add(t.author.screen_name)
    print("search_for_hashtags done")
    # return "hashtags"
    return hashtagsAuthor

def search_for_user_mentions(user_mentions):
    userMentionsAuthor = set()
    for u in user_mentions:
        # print("@" + u)
        tweets = api.search(q = "@" + u, rpp = 20)
        for t in tweets:
            userMentionsAuthor.add(t.author)
            # userMentionsAuthor.add(t.author.screen_name)
    print("search_for_user_mentions done")
    return userMentionsAuthor

def search_for_places(places):
    placesAuthor = set()
    for p in places:
        tweets = api.search(q = "place:" + p, rpp = 50)
        for t in tweets:
            # print(t.author.screen_name)
            # print(t.author.profile_image_url)
            # placesAuthor.add(t.author.screen_name)
            placesAuthor.add(t.author)

    # print placesAuthor
    print("search_for_places done")
    # return "places"
    return placesAuthor

def search_for_most_recent_loc(most_recent_loc):
    locAuthor = set()
    radius = "100mi"
    if most_recent_loc is None:
        return locAuthor
    places_list = api.reverse_geocode(lat = most_recent_loc[1], 
                                      long = most_recent_loc[0],
                                      accuracy = 10000,
                                      max_results = 50)
    for p in places_list:
        tweets = api.search(q = "place:" + p.id, rpp = 50)
        for t in tweets:
            # locAuthor.add(t.author.screen_name)
            locAuthor.add(t.author)

    print("search_for_most_recent_loc done")
    # return "location"
    return locAuthor

def search_for_retweets_of(retweets_of):
    retweetsOfAuthor = set()
    for r in retweets_of:
        if r is None:
            continue
        tweets = api.retweets(r)
        for t in tweets:
            # retweetsOfAuthor.add(t.author.screen_name)
            retweetsOfAuthor.add(t.author)

    print("search_for_retweets_of done")
    # return "retweets"
    return retweetsOfAuthor

# bind tweets author and type to the list
def bind_hashtags(h, friend_dict):
    if h.screen_name in friend_dict:
        friend_dict[h.screen_name].type += "hashtags "
    else:
        friend_dict[h.screen_name] = UserInfo(h.name, h.screen_name, h.profile_image_url, "0%", "hashtags ")

def bind_places(p, friend_dict):
    if p.screen_name in friend_dict:
        friend_dict[p.screen_name].type += "places "
    else:
        friend_dict[p.screen_name] = UserInfo(p.name, p.screen_name, p.profile_image_url, "0%", "places ")

def bind_locations(l, friend_dict):
    if l.screen_name in friend_dict:
        friend_dict[l.screen_name].type += "most_recent_loc "
    else:
        friend_dict[l.screen_name] = UserInfo(l.name, l.screen_name, l.profile_image_url, "0%", "most_recent_loc ")

def bind_retweets(r, friend_dict):
    if r.screen_name in friend_dict:
        friend_dict[r.screen_name].type += "retweets_of "
    else:
        friend_dict[r.screen_name] = UserInfo(r.name, r.screen_name, r.profile_image_url, "0%", "retweets_of ")
   

def generate_tweets_list(target_handle, target_tweets, friend_dict):
    tweets_list = collections.OrderedDict()
    tweets_list[target_handle] = combine_tweets(target_tweets)
    print(len(tweets_list))
    executor = concurrent.futures.ThreadPoolExecutor(16)
    futures = [executor.submit(get_tweets_and_combine, f, tweets_list) for f in friend_dict]
    concurrent.futures.wait(futures)

    print(len(tweets_list))
    # for t in tweets_list:
    #     print(t)
    #     print(tweets_list[t])
    #     print("generate_tweets_list done")    
    return tweets_list

def get_tweets_and_combine(friend, tweets_list):
    try:
        statuses, tweets = get_tweets(friend)
        tweets_list[friend] = combine_tweets(tweets)
    except tweepy.TweepError:
        print(friend)
        print(TweepError.message[0]['code'])
        print(TweepError.message[0]['text'])
        print(TweepError.message[0]['description'])
        # print("Failed to run the command on that user, Skipping...")


def printList(list):
    for l in list:
        print l

def printTweets(tweets):
    for t in tweets:
        print(t.text);



def calculate_similarity(tweets_list):
    # tf-idf vectorization
    from sklearn.feature_extraction.text import TfidfVectorizer
    

    tfidf = TfidfVectorizer().fit_transform(tweets_list.values())
    
    print(tfidf.shape)

    # do cosine similarity calculation
    from sklearn.metrics.pairwise import linear_kernel
    cosine_similarities = linear_kernel(tfidf[0:1], tfidf).flatten()

    print(len(cosine_similarities))
    rates = dict()
    i = 0
    for t in tweets_list:
        rates[t] = cosine_similarities[i]
        i += 1

    print("calculate_similarity done")
    return rates    

def combine_tweets(tweets):
    return " ".join(tweets)


# set the secret key.  keep this really secret:
# app.secret_key = 'A0Zr98j/3yX R~XHH!jmN]LWX/,?RT'
if __name__ == '__main__':
    app.run(debug = True)
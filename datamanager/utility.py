#DATA MANAGER APP - utility.py
#functions used by admin.py for creating data.

import requests
import re
from datetime import datetime, date
import ipdb
from django.db.models import Q


from bs4 import BeautifulSoup
from nameparser import HumanName

from games.models import Game, TeamGame, PlayerGame, ShiftGame, TeamGameEvent, LineGame, LineGameTime
from players.models import Player
from teams.models import Team

from .myfunks import convertToSecs, deleteAfter
from .eventprocessor import EventProcessor


class LineData(object):
#object used for storing game lines and accompanying data
	line_list = []
	start_time = None
	end_time = None
	shift_length = None

	def calculate_length(self):
	#calculates the length of time this shift has been on the ice
		length = self.end_time - self.start_time
		if self.start_time == None or self.end_time == None:
			raise(ValueError("start_time and end_time need to be set"))
		elif length < 0:
			raise (ValueError("start_time (%d) is greater than end_time (%d)" % (self.start_time, self.end_time)))
		else:
			self.shift_length = length



def get_front_page():
	r = requests.get('http://www.nhl.com/ice/gamestats.htm?fetchKey=20133ALLSATAll&sort=gameDate&viewName=teamRTSSreports')

	soup = BeautifulSoup(r.text)

	return soup

def get_soup(game_num, link_type):
	'''
	returns a beautiful soup object of different types of game data
	link_type is a string to specify data type - 'roster', 'shifts_home', 'shifts_away', or 'play_by_play'
	'''

	#extract season number from game_id and use it for the data source URL
	season_num = game_num / 1000000
	season_string = "20%d20%d" % (season_num, season_num + 1)

	#extract game_string from game_id and use it for the data source URL
	game_string = "%d" % game_num
	game_string = game_string[2:]

	if link_type == 'roster':
		r = requests.get('http://www.nhl.com/scores/htmlreports/' + season_string + '/RO' + game_string + '.HTM')

	elif link_type == 'shifts_home':
		r = requests.get('http://www.nhl.com/scores/htmlreports/' + season_string + '/TH' + game_string + '.HTM')

	elif link_type == 'shifts_away':
		r = requests.get('http://www.nhl.com/scores/htmlreports/' + season_string + '/TV' + game_string + '.HTM')

	elif link_type == 'play_by_play':
		r = requests.get('http://www.nhl.com/scores/htmlreports/' + season_string + '/PL' + game_string + '.HTM')

	else:
		raise(ValueError("Valid link_type arguments are 'roster', 'play_by_play', 'shifts_home', and 'shifts_away'"))


	soup = BeautifulSoup(r.text)

	if soup.title.text == "404 Not Found":
		raise(ValueError("Data could not be found at this address for game %s in season %s" % (game_string, season_string)))

	return soup


def create_game(game_num, roster_soup):

	#pull team name data from txt - first two instances of class teamHeading
	teams = roster_soup.find_all('td', class_ = "teamHeading")
	away_name = teams[0].text.encode('ASCII')
	home_name = teams[1].text.encode('ASCII')

	#Creates TeamGame objects home, away
	away = TeamGame.objects.create(team = Team.objects.get(name=away_name))
	home = TeamGame.objects.create(team = Team.objects.get(name=home_name))


	game = Game(game_id=game_num, team_home=home, team_away=away, date=import_date(roster_soup))	
	game.save()

	return game



def import_player_data(team, roster_soup):

	#Determines if the team is the home or away team for the game
	if team.is_home():
		homeaway = "home"
		game_id = team.home.all()[0].game_id
	elif team.is_away():
		homeaway = "away"
		game_id = team.away.all()[0].game_id
	else:
		raise(Exception("TeamGame object (%s) has no home or away status") % team)

	##GET ROSTER
	

	#Indicates the list index of "roster" from which to begin iterating
	START_ROSTER = 3

	#Indices of player data within each player's 'roster' tags
	POSITION_INDEX = 3
	NAME_INDEX = 5
	NUMBER_INDEX = 1

	#number is a value to determine which index of the BeautifulSoup object to extract data from
	if homeaway == "home":
		number = 1
	else:
		number = 0

	#Sets 'roster' to a BeautifulSoup object which contains the data for a team's roster
	roster = roster_soup.find_all("td", text="#")[number].parent.parent

	#Iterates over the 'roster', skipping every other item (null tags)
	for i in range(START_ROSTER, len(roster), 2):

		position = roster.contents[i].contents[POSITION_INDEX].text.encode('ASCII')

		name = roster.contents[i].contents[NAME_INDEX].text.encode('ASCII')
		hname = HumanName(name)
		first_name = hname.first
		last_name = hname.last

		number = roster.contents[i].contents[NUMBER_INDEX].text.encode('ASCII')

		#Checks if player exists or if multiple players with this name.  
		#If player does not exist, add to Player database and PlayerGame database.
		#If player exists, add to PlayerGame database.
		try:
			p = Player.objects.get(first_name=first_name, last_name=last_name, team=team.team, number=number)
			PlayerGame.objects.create(player=p, team=team)
		except Player.MultipleObjectsReturned:
			#Check if multiple players in the league or if player has been traded
			print "Multiple players returned"
			pass
		except Player.DoesNotExist:
			#Check if new player or data error
			p = Player.objects.create(first_name=first_name, last_name=last_name, position=position, number=number, team=team.team)
			PlayerGame.objects.create(player=p, team=team)
			print "Added - " + p.first_name + " " + p.last_name + " (" + p.position + ") " + team.team.initials 
	
	
	###GET SHIFTS

	if homeaway == "home":
		shift_soup = get_soup(game_id, 'shifts_home')
	else:
		shift_soup = get_soup(game_id, 'shifts_away')

	#Get list of players from BeautifulSoup object
	players = shift_soup.find_all('td', class_='playerHeading')
	
	for player in players:
		playerName = HumanName(player.text)

		#Strip out player's number from name
		playerName.last = re.sub("\d+", "", playerName.last).strip()
		playerObj = Player.objects.get(first_name=playerName.first, last_name=playerName.last)

		#Create new object for PlayerGame and save to database
		playerGameObj = PlayerGame.objects.get(player=playerObj, team=team)

		#Parse the individual player BeautifulSoup object for shift data
		shift = player.parent.next_sibling.next_sibling.next_sibling.next_sibling
		shift_list = shift.find_all("td")

		#If 6 items in shift_list object, it matches the structure of valid shift data
		while(len(shift_list) == 6):
			period = int(shift_list[1].text)
			timeOn = deleteAfter(shift_list[2].text, '/')
			timeOn = convertToSecs(timeOn, period)
			timeOff = deleteAfter(shift_list[3].text, '/')
			timeOff = convertToSecs(timeOff, period)


			ShiftGame.objects.create(playergame=playerGameObj, start_time=timeOn, end_time=timeOff)

			#Get next shift for this player
			shift = shift.next_sibling.next_sibling
			shift_list = shift.find_all("td")

	ShiftGame.objects.filter(playergame__player__position='G').delete()


def make_lines(teamgame):

	def import_line(line, pos_type):

		try:
			filtered_line = {'D' : line.filter(playergame__player__position='D'),
							'F' : line.filter(Q(playergame__player__position='C') | 
									Q(playergame__player__position='R') | 
									Q(playergame__player__position='L'))

			}[pos_type.upper()]
		except KeyError:
			print "Invalid paramater in pos_type.  Accepts 'F','D'"

		line_object = teamgame.get_line(filtered_line)
		#ipdb.set_trace()

		if line_object:

			if (LineGameTime.objects.filter(linegame=line_object, end_time__gte=shift_end).count() == 0):
				line_object.ice_time += shift_length
				line_object.num_shifts += 1
				line_object.save()

				LineGameTime.objects.create(linegame = line_object, start_time=shift_start, end_time=shift_end, ice_time=shift_length)
		else:
			line_add = LineGame.objects.create(teamgame = teamgame, 
				num_players = len(filtered_line),
				ice_time = shift_length,
				num_shifts = 1,
				goals = 0,
				hits = 0,
				blocks = 0,
				shots = 0,
				linetype = pos_type)

			LineGameTime.objects.create(linegame = line_add, start_time=shift_start, end_time=shift_end, ice_time=shift_length)

			for shiftgame in filtered_line:
				line_add.playergames.add(shiftgame.playergame)







	###############################################


	prev_end = 0
	prev_line = None
	game_end = ShiftGame.objects.filter(playergame__team=teamgame).reverse()[0].end_time
		
	#Loop until end of the game is the lowest time of a line
	while(prev_end < game_end):
		
		exception_case = False
		current_line = ShiftGame.objects.filter(playergame__team=teamgame, start_time__lte=prev_end, end_time__gt=prev_end)

		#if a line comes out with more players than the previous line, some work needs to be done to figure out
		#what time the extra player came on the ice.
		if (prev_line and current_line.count() > prev_line.count()):
			#check if the current group all started at the same time.  no need to proceed if so.
			if not is_new_group(current_line):
				exception_case = True
				#ipdb.set_trace()

				prev_end = compare_groups(prev_line, current_line, prev_end)

				prev_line_set = LineGameTime.objects.filter(
					linegame__teamgame=teamgame, start_time__lte=prev_end, end_time__gt=prev_end)
				
				for prev_line_obj in prev_line_set:
					prev_line_obj.end_time = prev_end
					prev_line_obj.save()

				current_line = ShiftGame.objects.filter(playergame__team=teamgame, start_time__lte=prev_end, end_time__gt=prev_end)
				#ipdb.set_trace()

		lowest_time = get_lowest(current_line)

		shift_start = prev_end
		shift_end = lowest_time

		#if an exception case, need to make sure this line doesn't overlap with next line


		shift_length = shift_end - shift_start

		import_line(current_line, 'F')
		import_line(current_line, 'D')

		#if exception_case:
		#	ipdb.set_trace()

		#get offensive players
		prev_end = lowest_time
		prev_line = current_line

		#ipdb.set_trace()


def is_new_group(shifts):
	#iterates through a ShiftGame query, and returns True if all players started shift at the same time
	start_time = shifts.all()[0].start_time
	for player in shifts.all()[1:]:
		if player.start_time != start_time:
			return False
	
	return True

def get_lowest(shifts):
	#iterates through a ShiftGame query, returns the lowest end_time attribute value

	lowest_end = 99999
	for shift in shifts:
		if shift.end_time < lowest_end:
			lowest_end = shift.end_time

	return lowest_end


def compare_groups(prev_line, current_line, exclude_time):
	#compares two ShiftGame queries and returns the start time for
	# the first shift that exists in current_line but not prev_line
	# AND doesn't have a start time of ::exclude_time::
	#Used for detecting special case players that come on the ice 
	# without replacing another player

	q = current_line.exclude(start_time__gte = exclude_time)
	for this_player in prev_line:
		q = q.exclude(playergame=this_player.playergame)

	if q.count() == 0:
		return exclude_time

	return q[0].start_time


'''
		if line_object:
			line_object.ice_time += shift_length
			line_object.num_shifts += 1
			line_object.save()

			LineGameTime.objects.create(linegame = line_object, start_time=shift_start, end_time=shift_end, ice_time=shift_length)
			print current_line_list
		else:
			line_model = LineGame.objects.create(teamgame = teamgame, 
				num_players = len(current_line_list),
				ice_time = shift_length,
				num_shifts = 1,
				goals = 0,
				hits = 0,
				blocks = 0,
				shots = 0)

			LineGameTime.objects.create(linegame = line_model, start_time=shift_start, end_time=shift_end, ice_time=shift_length)

			for shiftgame in current_line_list:
				line_model.playergames.add(shiftgame.playergame)

			

		#set a new value prev_end to be used in the next iteration
		prev_end = lowest_time

		line_list.append(current_line_list)

		#reset line_object for next loop
		line_object = None

'''



def import_events(game):
#Pull game event data from an EventProcessor object and add to the database in the Games model.

	def increment_event(teamgame, event):
		#ipdb.set_trace()
		#when querying the line, select the time at event_time-1 to make sure it takes the end of the shift rather than the beginning
		try:
			lgt = LineGameTime.objects.get(linegame__playergames__player__number=event.event_player, linegame__teamgame=teamgame, start_time__lte=event.event_time_in_seconds-1, end_time__gt=event.event_time_in_seconds-1)
		except:
			try:
				lgt = LineGameTime.objects.get(linegame__playergames__player__number=event.event_player, linegame__teamgame=teamgame, start_time__lte=event.event_time_in_seconds, end_time__gt=event.event_time_in_seconds)
			except:
				print "%s %s not found at %s (pd %s) -  %s" % (event.event_team_initials, event.event_player, event.event_time, event.event_period, event.event_type.upper())
				ipdb.set_trace()
				return
		line = lgt.linegame


		#print event.event_type
		#print event.event_time_in_seconds
		if event.event_type == 'SHOT':
			line.shots += 1
		elif event.event_type == 'HIT':
			line.hits += 1
		elif event.event_type == 'BLOCK':
			line.blocks += 1
		elif event.event_type == 'GOAL':
			line.goals += 1
		else:
			raise(ValueError, "Event type not valid")

		player = PlayerGame.objects.get(team=teamgame, player__number=event.event_player)
		line.save()
		TeamGameEvent.objects.create(playergame=player, event_time=event.event_time_in_seconds, event_type=event.event_type, linegame=line)



	event_soup = get_soup(game.pk, 'play_by_play')

	events = EventProcessor(event_soup)

	#get objects and initials for home and away teams
	home_team_initials = game.team_home.team.initials
	away_team_initials = game.team_away.team.initials

	for event in events.flatten():
		if event.event_team_initials == home_team_initials:
			team = game.team_home
		elif event.event_team_initials == away_team_initials:
			team = game.team_away
		else:
			raise(ValueError("Event team initials (%s) do not match data" % event.event_team_initials))

		increment_event(team, event)


def import_date(soup):
	#Pull a date from the roster file.  ROSTER FILE ONLY

	days = ["^Sunday,", "^Monday,", "^Tuesday,", "^Wednesday,", "^Thursday,", "^Friday,", "^Saturday,"]
	date_text = None

	for day in days:
		search_str = re.compile(day)
		date_soup = soup.find('td', text=search_str)

		if date_soup:
			break


	if not date_soup:
		raise(Exception, "No date found in soup file")

	date_text = date_soup.text
	date_text = date_text[date_text.index(',')+1:].strip()

	temp_date = datetime.strptime(date_text, "%B %d, %Y")
	return date(temp_date.year, temp_date.month, temp_date.day)

	
import os
import io
import json
import requests
import urllib3
import pandas as pd
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_original_send = requests.Session.send
def _send_no_verify(self, *args, **kwargs):
    kwargs['verify'] = False
    return _original_send(self, *args, **kwargs)
requests.Session.send = _send_no_verify

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

login_manager = LoginManager(app)
login_manager.login_view = "login"

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

class User(UserMixin):
    def __init__(self, id, email, name, picture):
        self.id = id
        self.email = email
        self.name = name
        self.picture = picture

_users = {}

@login_manager.user_loader
def load_user(user_id):
    return _users.get(user_id)

db = WorkspaceClient()
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")

CONTACT_METHODS = [
    "delivery", "door_knock", "email", "email_blast", "face_to_face",
    "facebook", "meeting", "phone_call", "robocall", "snail_mail",
    "text", "text_1to1", "text_blast", "tweet", "video_call",
    "webinar", "linkedin", "other",
]
CONTACT_STATUSES = [
    "answered", "bad_info", "left_message", "meaningful_interaction",
    "send_information", "not_interested", "no_answer", "refused",
    "inaccessible", "other",
]

_NICKNAME_GROUPS = [
    # Male
    ("harry", "harrison", "harold", "henry", "hank", "hal", "harris", "harvey"),
    ("bill", "billy", "william", "will", "willy", "liam", "willem"),
    ("bob", "bobby", "robert", "rob", "robbie", "bert", "bertie"),
    ("jim", "jimmy", "james", "jamie", "jake"),
    ("joe", "joey", "joseph", "jose"),
    ("jack", "john", "johnny", "jon", "jonathan", "jacky"),
    ("mike", "mikey", "michael", "mick", "mickey"),
    ("dave", "david", "davy", "davey"),
    ("tom", "tommy", "thomas"),
    ("rick", "ricky", "richard", "dick", "rich", "richie"),
    ("nick", "nicky", "nicholas", "nicolas", "nico"),
    ("chris", "christopher", "christian", "cris", "kris"),
    ("dan", "danny", "daniel", "dani"),
    ("al", "albert", "alfred", "alan", "ali", "alfie"),
    ("ed", "eddie", "edward", "edgar", "ned", "ted", "ward"),
    ("frank", "frankie", "francis", "fran"),
    ("fred", "freddy", "frederick", "fritz"),
    ("jerry", "gerald", "jerome", "gerry"),
    ("ken", "kenny", "kenneth"),
    ("larry", "lawrence", "lars", "laurence"),
    ("pete", "peter", "petey"),
    ("ray", "raymond"),
    ("ron", "ronnie", "ronald"),
    ("steve", "steven", "stephen", "stevo"),
    ("tim", "timmy", "timothy"),
    ("tony", "anthony", "ant"),
    ("andy", "andrew", "drew"),
    ("ben", "benny", "benjamin"),
    ("brad", "bradley", "bradford"),
    ("charlie", "charles", "chuck", "chas", "carl", "carlos"),
    ("don", "donald", "donnie"),
    ("doug", "douglas", "dougie"),
    ("greg", "gregory", "gregg"),
    ("jeff", "jeffrey", "geoff", "geoffrey"),
    ("josh", "joshua"),
    ("matt", "matthew", "matty"),
    ("max", "maxwell", "maximilian", "maxine", "maxime"),
    ("nat", "nate", "nathan", "nathaniel"),
    ("neil", "neal", "nigel"),
    ("norm", "norman"),
    ("ollie", "oliver"),
    ("phil", "phillip", "philip"),
    ("rod", "rodney", "roderick"),
    ("russ", "russell"),
    ("sam", "samuel", "sammy"),
    ("stan", "stanley", "stanford"),
    ("stu", "stuart", "stewart"),
    ("theo", "theodore"),
    ("vince", "vincent", "vinny"),
    ("walt", "walter", "wally"),
    ("wes", "wesley", "weston"),
    ("woody", "woodrow"),
    ("zach", "zachary", "zack", "zak"),
    ("alex", "alexander", "alec", "lex"),
    ("art", "arthur", "artie"),
    ("bart", "barton", "bartholomew"),
    ("bud", "buddy"),
    ("cal", "calvin", "caleb"),
    ("chet", "chester"),
    ("clint", "clinton"),
    ("cy", "cyrus"),
    ("denny", "dennis", "denis"),
    ("dom", "dominic", "dominick"),
    ("dusty", "dustin"),
    ("eli", "elijah", "elias"),
    ("ernie", "ernest"),
    ("gabe", "gabriel"),
    ("gene", "eugene"),
    ("gil", "gilbert"),
    ("gus", "augustus", "angus", "gustav"),
    ("herb", "herbert"),
    ("ike", "isaac"),
    ("jay", "james", "jason", "jacob"),
    ("jeff", "jeffrey"),
    ("leo", "leonard", "leon"),
    ("les", "leslie", "lester"),
    ("lew", "lewis", "louis", "lou"),
    ("mac", "malcolm", "mack"),
    ("manny", "manuel", "emmanuel"),
    ("marty", "martin"),
    ("mel", "melvin"),
    ("mitch", "mitchell"),
    ("monty", "montgomery"),
    ("mort", "morton", "mortimer"),
    ("moe", "morris", "moses"),
    ("ozzy", "oscar", "oswald"),
    ("percy", "percival"),
    ("reg", "reginald"),
    ("rex", "reginald"),
    ("rudy", "rudolph", "rudolf"),
    ("scott", "scotty"),
    ("sean", "shaun", "shawn"),
    ("sherm", "sherman"),
    ("sid", "sydney", "sylvester"),
    ("skip", "james", "john"),
    ("sol", "solomon"),
    ("sonny", "sylvester"),
    ("tad", "thaddeus"),
    ("trey", "trevor", "trenton"),
    ("zeke", "ezekiel"),
    ("rand", "randall", "randolph", "randy"),
    ("reggie", "reginald"),
    ("rocky", "rockwell"),
    ("ross", "roscoe"),
    ("dex", "dexter"),
    ("kurt", "curtis", "curt"),
    ("wade", "walden"),
    ("xander", "alexander"),
    ("emmett", "emmet"),
    ("glen", "glenn"),
    ("heath", "heathcliff"),
    ("jed", "jedidiah"),
    ("kip", "kipling"),
    ("lyle", "leland"),
    ("marsh", "marshall", "marcus"),
    ("murray", "muriel"),
    ("otis", "otto"),
    ("rad", "radley"),
    ("rush", "russell"),
    ("sly", "sylvester"),
    ("tanner", "tan"),
    # Female
    ("abby", "abigail", "abbey"),
    ("addie", "adelaide", "adeline", "ada"),
    ("aggie", "agnes"),
    ("allie", "alice", "allison", "alyssa"),
    ("angie", "angela", "angelina"),
    ("annie", "ann", "anna", "anne", "annette"),
    ("bea", "beatrice", "beatrix"),
    ("bella", "isabella", "arabella", "isabel"),
    ("bess", "bessie", "elizabeth"),
    ("beth", "elizabeth", "bethany"),
    ("betsy", "elizabeth"),
    ("betty", "elizabeth", "bette"),
    ("billie", "wilhelmina"),
    ("bonnie", "bonita"),
    ("bree", "brianna", "breanna", "sabrina"),
    ("brit", "brittany", "britney"),
    ("callie", "carolyn", "caroline", "calista"),
    ("carrie", "caroline", "carol", "carolyn", "carla", "carly"),
    ("cass", "cassandra", "cassidy"),
    ("cat", "catherine", "catalina", "caitlin"),
    ("cathy", "catherine", "kathleen", "kathryn"),
    ("cece", "cecelia", "cecilia"),
    ("cher", "cheryl", "sharon", "cherie"),
    ("cindy", "cynthia", "lucinda"),
    ("claire", "clara", "clarissa"),
    ("connie", "constance", "cornelia"),
    ("cora", "corinne", "cordelia"),
    ("debbie", "deborah", "deb", "debra"),
    ("dee", "deanna", "diana", "delia"),
    ("dora", "dorothy", "theodora", "dorothea"),
    ("dot", "dorothy"),
    ("ellie", "eleanor", "ellen", "elizabeth", "elena", "elaine"),
    ("emma", "emily", "emmeline"),
    ("eva", "evelyn", "evangeline", "eve"),
    ("flo", "florence", "flora"),
    ("frankie", "frances", "francesca", "francine"),
    ("gail", "abigail", "gayle"),
    ("ginny", "virginia", "geneva"),
    ("gwen", "gwendolyn", "gwyneth", "guinevere", "gwenna"),
    ("hattie", "harriet", "henrietta"),
    ("harriet", "harriet", "hattie"),
    ("jackie", "jacqueline"),
    ("janie", "jane", "janet", "jan"),
    ("jess", "jessica", "jessamine", "jessie"),
    ("jo", "josephine", "joan", "joanna"),
    ("josie", "josephine"),
    ("jules", "julia", "juliet", "julie"),
    ("kate", "katie", "kathy", "katherine", "catherine", "kathryn", "kay"),
    ("kim", "kimberly"),
    ("kitty", "katherine", "kathryn", "kathleen"),
    ("laurie", "laura", "lauren", "laurel", "lori"),
    ("lexi", "alexandra", "alexis"),
    ("libby", "elizabeth"),
    ("lilly", "lillian", "lily"),
    ("liz", "elizabeth", "lisa", "beth", "eliza"),
    ("lola", "dolores", "charlotte"),
    ("lori", "lorraine", "laura", "loretta"),
    ("lottie", "charlotte", "carlotta"),
    ("lu", "lucy", "lucia", "lucille", "louisa"),
    ("lucy", "lucia", "lucille", "lucinda"),
    ("mae", "mary", "margaret", "may"),
    ("maggie", "margaret", "magdalene", "mags"),
    ("mandy", "amanda", "miranda"),
    ("margie", "margaret", "margery", "marge"),
    ("meg", "margaret", "megan"),
    ("mel", "melissa", "melinda", "melanie"),
    ("millie", "mildred", "millicent", "emily", "camille", "amelia"),
    ("minnie", "wilhelmina", "minerva", "mina"),
    ("missy", "melissa"),
    ("molly", "mary", "margaret"),
    ("nan", "nancy", "ann", "nanette"),
    ("nell", "eleanor", "ellen", "helen", "nellie"),
    ("nora", "eleanor", "honora", "leonora"),
    ("pam", "pamela"),
    ("pat", "patricia", "patience"),
    ("patty", "patricia", "martha"),
    ("penny", "penelope"),
    ("peg", "peggy", "margaret"),
    ("polly", "mary", "paula", "pauline"),
    ("rita", "margaret", "margarita"),
    ("rose", "rosemary", "rosalyn", "rosalie"),
    ("rosie", "rosemary", "rose", "rosanna"),
    ("sadie", "sarah"),
    ("sally", "sarah"),
    ("sam", "samantha", "samara"),
    ("sandy", "sandra", "cassandra"),
    ("sasha", "alexandra", "natasha"),
    ("sherry", "sharon", "charlotte"),
    ("sonya", "sophia", "sonia"),
    ("sophie", "sophia"),
    ("steph", "stephanie", "stefanie"),
    ("sue", "susan", "suzy", "susanna"),
    ("tammy", "tamara"),
    ("tess", "theresa", "teresa", "tessa"),
    ("tina", "christina", "valentina", "martina"),
    ("trudy", "gertrude"),
    ("val", "valerie", "valentine"),
    ("vicki", "victoria"),
    ("viv", "vivienne", "vivian"),
    ("winnie", "winifred", "winona"),
    ("becca", "rebecca", "becky"),
    ("cam", "cameron", "camille", "camilla"),
    ("chrissy", "christine", "christina"),
    ("dani", "danielle", "daniela"),
    ("elsa", "elizabeth", "eleanor"),
    ("gabby", "gabrielle", "gabriela"),
    ("hallie", "harriet"),
    ("jodi", "judith", "jody"),
    ("lena", "elena", "helen", "magdalena", "selena"),
    ("mia", "maria", "miriam", "amelia"),
    ("nicki", "nicole", "nicola"),
    ("nina", "annina", "antonina"),
    ("nola", "magnolia", "cornelia"),
    ("rae", "rachel", "renee"),
    ("roxy", "roxanne", "roxanna"),
    ("ruby", "rubina"),
    ("shelly", "michelle", "rochelle"),
    ("terri", "theresa", "teresa", "terrence"),
    ("tori", "victoria"),
    ("trish", "patricia", "tricia"),
    ("wendy", "gwendolyn"),
    ("barb", "barbara"),
    ("carol", "caroline", "carolyn"),
    ("linda", "belinda"),
    ("jen", "jenny", "jennifer"),
    # Gender-neutral
    ("alex", "alexander", "alexandra", "alexis"),
    ("ashley", "ash"),
    ("casey", "cassidy"),
    ("devon", "devonne"),
    ("drew", "andrew", "andrea"),
    ("hayden", "hayley"),
    ("jamie", "james", "jamison"),
    ("jordan", "jordy"),
    ("kelly", "kelsey"),
    ("lee", "leona", "leo"),
    ("leslie", "les"),
    ("morgan", "morgana"),
    ("quinn", "quincy"),
    ("riley", "reilly"),
    ("robin", "roberta", "robert"),
    ("rory", "aurora", "roderick"),
    ("ryan", "ryanne"),
    ("shannon", "shauna", "shana"),
    ("shelby", "sheila"),
    ("skyler", "sky"),
    ("stacy", "anastasia"),
    ("taylor", "tay"),
    ("tracey", "tracy", "theresa"),
    ("whitney", "whit"),
    ("terry", "theresa", "teresa", "terrence"),
    # Additional female names
    ("kit", "katherine", "kathryn", "kathleen", "kristin", "kristina"),
    ("gwen", "gwendolyn", "gwyneth", "guinevere", "gwenna"),
    ("teddy", "theodora", "theodore", "edwina", "edna"),
    ("winnie", "winifred", "winnifred", "winona"),
    ("gabby", "gabrielle", "gabriela", "gabriella", "gabi"),
    ("dottie", "dorothy", "dot"),
    ("elspeth", "elizabeth", "elspie"),
    ("flossie", "florence", "flora", "flo"),
    ("georgie", "georgina", "georgiana", "georgia"),
    ("hattie", "harriet", "henrietta", "hatty"),
    ("issy", "isabelle", "isabella", "isabel", "isobel"),
    ("jojo", "josephine", "joanna", "joanne"),
    ("lala", "laura", "lara", "larissa"),
    ("lettie", "letitia", "leticia", "violet"),
    ("lexie", "alexandra", "alexis", "lexi"),
    ("liddy", "lydia"),
    ("lilah", "delilah", "lillian", "lila"),
    ("lindy", "linda", "belinda", "melinda"),
    ("livvy", "olivia", "livia"),
    ("lottie", "charlotte", "carlotta"),
    ("lu", "louisa", "lucy", "lucille", "lucia", "lucinda"),
    ("maddie", "madeleine", "madeline", "madelyn", "madison"),
    ("mae", "mary", "margaret", "may", "maeve"),
    ("maisie", "margaret", "mary", "miriam"),
    ("mally", "malinda", "melinda", "mallory"),
    ("marnie", "marina", "marianne", "maren"),
    ("mattie", "martha", "matilda"),
    ("maude", "madeleine", "magdalene"),
    ("midge", "margaret", "miriam"),
    ("milly", "mildred", "millicent", "camille", "amelia", "emily"),
    ("mindy", "melinda", "miranda", "amanda"),
    ("miri", "miriam", "mirielle"),
    ("nettie", "annette", "antoinette", "henrietta"),
    ("nicky", "nicole", "nicola", "nicolette"),
    ("nixie", "nicole", "nicola"),
    ("nonnie", "nora", "eleanor", "honora"),
    ("olga", "helga"),
    ("opal", "ophelia"),
    ("pippa", "philippa", "penelope"),
    ("posy", "josephine", "rose", "rosemary"),
    ("prissy", "priscilla"),
    ("queenie", "regina", "queen"),
    ("remi", "remy", "remington"),
    ("rena", "irene", "serena", "katerina", "renata"),
    ("retta", "henrietta", "loretta", "marietta"),
    ("ricki", "erica", "frederica", "rica"),
    ("rilla", "amarilla", "marilla"),
    ("romie", "rosemary", "rome"),
    ("roni", "veronica", "rhonda"),
    ("ronnie", "veronica", "rhonda", "rhona"),
    ("rosalie", "rose", "rosemary", "rosalyn"),
    ("roxie", "roxanne", "roxanna"),
    ("ruthie", "ruth"),
    ("sammie", "samantha", "samara"),
    ("sandi", "sandra", "cassandra", "sandy"),
    ("sibby", "sibyl", "sybil"),
    ("sissy", "cecelia", "cecilia"),
    ("stella", "estelle", "estrella"),
    ("sukey", "susan", "susannah"),
    ("suki", "susan", "suzette", "susannah"),
    ("sylvie", "sylvia", "silvana"),
    ("tabi", "tabitha", "tabby"),
    ("tallie", "natalie", "talia"),
    ("tessie", "theresa", "teresa", "tessa"),
    ("thea", "theodora", "dorothea", "theresa"),
    ("tibby", "isabella", "tibbie"),
    ("tillie", "matilda", "ottilie"),
    ("toby", "october", "tobitha"),
    ("tommie", "tamara", "tamsin"),
    ("toni", "antonia", "antoinette"),
    ("totie", "dorothy", "victoria"),
    ("tricia", "patricia", "beatrice"),
    ("trina", "katrina", "marina", "carolina"),
    ("trudie", "gertrude", "trudy"),
    ("una", "ursula", "unity"),
    ("vera", "veronica", "lavera"),
    ("vinnie", "lavinia", "virginia"),
    ("vita", "victoria", "davita"),
    ("vonnie", "yvonne", "lavonne"),
    ("wanda", "wenda", "gwendolyn"),
    ("xena", "alexena", "zena"),
    ("yola", "yolanda"),
    ("zelda", "griselda"),
    ("zena", "zenobia", "xena"),
    ("zia", "rosalia", "zenobia"),
    ("zosia", "sophia", "zoe"),
    # Additional male names
    ("abe", "abraham", "abner"),
    ("ace", "ace"),
    ("alf", "alfred", "alphonso"),
    ("archie", "archibald", "archer"),
    ("arnie", "arnold"),
    ("aug", "augustus", "augustine"),
    ("augie", "augustus", "augustine"),
    ("barney", "barnabas", "barnaby", "bernard"),
    ("basil", "basil"),
    ("bernie", "bernard", "bernardo"),
    ("bertie", "albert", "robert", "bert", "bertram"),
    ("biff", "william", "clifford"),
    ("birch", "birch"),
    ("bix", "bixby"),
    ("blaine", "blane"),
    ("blake", "blackwell"),
    ("bo", "robert", "beauregard"),
    ("bram", "abraham", "bramwell"),
    ("brice", "bryce"),
    ("bronson", "bronco"),
    ("brook", "brooks"),
    ("bucky", "buckley", "william"),
    ("butch", "william", "james"),
    ("cam", "cameron", "campbell"),
    ("carey", "carlisle", "charles"),
    ("cash", "cassius", "casper"),
    ("caz", "casimir", "caspar"),
    ("chad", "chadwick"),
    ("chipper", "charles"),
    ("cj", "christopher", "charles"),
    ("clem", "clement", "clementine"),
    ("cliff", "clifford", "clifton"),
    ("coby", "jacob", "coby"),
    ("con", "cornelius", "constantine"),
    ("cord", "cordell", "gordon"),
    ("cory", "cornelius", "corey"),
    ("cosmo", "cosimo"),
    ("curt", "curtis", "courtney"),
    ("dab", "dabney"),
    ("dawson", "david"),
    ("dex", "dexter"),
    ("dirk", "derek", "richard"),
    ("dolph", "rudolph", "adolph"),
    ("duke", "marmaduke", "stanford"),
    ("dunc", "duncan"),
    ("dwight", "dewight"),
    ("dyl", "dylan"),
    ("eli", "elijah", "elias", "elliot"),
    ("elliot", "elliot", "elias"),
    ("elmo", "elmwood"),
    ("emmet", "emmett", "emory"),
    ("erv", "ervin", "irving"),
    ("ev", "evan", "evander"),
    ("finn", "finnegan", "phineas"),
    ("flip", "philip", "phillip"),
    ("ford", "fordham", "stanford"),
    ("forrest", "forest"),
    ("fox", "foxworth"),
    ("gib", "gilbert", "gibson"),
    ("gordie", "gordon"),
    ("gray", "grayson"),
    ("ham", "hamilton", "hamlet"),
    ("harley", "harold", "harlan"),
    ("howie", "howard"),
    ("huck", "huckleberry"),
    ("iggy", "ignatius", "ignacio"),
    ("ira", "irving", "israel"),
    ("irv", "irving", "irvin"),
    ("izzy", "isidore", "israel", "isaiah"),
    ("jace", "jason", "jacoby"),
    ("jasper", "caspar"),
    ("jb", "james", "john"),
    ("jeb", "jebediah", "james"),
    ("jed", "jedidiah", "edgar"),
    ("jenks", "jenkins"),
    ("jesse", "jessamyn"),
    ("jett", "jethro"),
    ("jimbo", "james"),
    ("joab", "joab"),
    ("jock", "john", "james"),
    ("joel", "joel"),
    ("jonah", "jonas"),
    ("jordy", "jordan", "george"),
    ("jud", "judah", "judson"),
    ("kaz", "casimir", "kazimir"),
    ("kenji", "kenneth"),
    ("kev", "kevin"),
    ("lars", "lawrence", "laurence"),
    ("laz", "lazarus"),
    ("len", "leonard", "lennox"),
    ("lenny", "leonard", "lennox"),
    ("leo", "leonard", "leopold", "leon"),
    ("levi", "levi"),
    ("linc", "lincoln"),
    ("link", "lincoln"),
    ("luca", "lucas", "luke", "lucian"),
    ("luke", "lucas", "lucian"),
    ("mace", "mason", "macy"),
    ("manny", "manuel", "emmanuel", "immanuel"),
    ("marco", "mark", "marcus"),
    ("mars", "marcus", "marshall"),
    ("mort", "mortimer", "morton"),
    ("moss", "moses", "morris"),
    ("noel", "noel"),
    ("obie", "obadiah"),
    ("ogden", "ogden"),
    ("olin", "oliver", "olaf"),
    ("oz", "oswald", "oscar", "ozzy"),
    ("paddy", "patrick", "padraig"),
    ("pax", "paxton"),
    ("pip", "philip", "phillip"),
    ("raj", "rajesh", "rajan"),
    ("rand", "randall", "randolph", "randy"),
    ("reef", "reginald"),
    ("reid", "reid"),
    ("ren", "reginald", "renault"),
    ("rhys", "reece", "reese"),
    ("rowan", "rowland"),
    ("rowdy", "rowland"),
    ("royce", "royston"),
    ("rube", "rueben", "reuben"),
    ("rupert", "robert", "ruprecht"),
    ("rush", "rushford"),
    ("saul", "solomon"),
    ("scotty", "scott", "scottie"),
    ("seb", "sebastian"),
    ("sergei", "sergius"),
    ("shep", "shepherd", "stephen"),
    ("sig", "sigmund", "siegfried"),
    ("silas", "silvester"),
    ("soren", "sorin"),
    ("spence", "spencer"),
    ("tate", "tatham"),
    ("tex", "texas", "theodore"),
    ("thad", "thaddeus"),
    ("tobias", "toby"),
    ("tuck", "tucker"),
    ("ty", "tyler", "tyrone", "tyson"),
    ("ulric", "ulrich"),
    ("van", "vance", "vanderbilt"),
    ("vic", "victor", "vincent"),
    ("wade", "walden", "waldron"),
    ("ward", "edward", "howard", "wardell"),
    ("wash", "washington"),
    ("webb", "webber"),
    ("wiley", "william", "riley"),
    ("winton", "winston"),
    ("wolf", "wolfgang", "wolfe"),
    ("woody", "woodrow", "elwood"),
    ("wyatt", "wyat"),
    ("york", "yorkshire"),
    ("zane", "zachariah", "john"),
]

# Build flat lookup map — any name in a group finds all others in that group
NICKNAME_MAP = {}
for _group in _NICKNAME_GROUPS:
    _variants = list(dict.fromkeys(v.lower() for v in _group))
    for _name in _variants:
        if _name not in NICKNAME_MAP:
            NICKNAME_MAP[_name] = _variants
        else:
            NICKNAME_MAP[_name] = list(dict.fromkeys(NICKNAME_MAP[_name] + _variants))


POSITIONAL_NICKNAMES = {
    "junior": "jr",
    "deuce": "ii",
    "trey": "iii",
    "tres": "iii",
    "trip": "iii",
    "tripp": "iii",
    "triple": "iii",
    "quad": "iv",
    "quint": "v",
    "five": "v",
    "six": "vi",
}

NAME_SUFFIXES = {
    "jr", "jr.", "junior",
    "sr", "sr.", "senior",
    "ii", "iii", "iv", "v", "vi", "vii",
    "2nd", "3rd", "4th", "5th",
    "the second", "the third", "the fourth",
    "esq", "esq.", "esquire",
    "phd", "md", "dds", "dvm",
}


def strip_suffix(name: str) -> tuple[str, str | None]:
    """Returns (clean_name, suffix_found_or_None)."""
    parts = name.strip().split()
    if len(parts) > 1 and parts[-1].lower().rstrip(".") in NAME_SUFFIXES:
        return " ".join(parts[:-1]), parts[-1]
    if len(parts) > 2 and " ".join(parts[-2:]).lower() in NAME_SUFFIXES:
        return " ".join(parts[:-2]), " ".join(parts[-2:])
    return name.strip(), None


def get_name_variants(name: str) -> list:
    n = name.lower().strip()
    if n in NICKNAME_MAP:
        return NICKNAME_MAP[n]
    for variants in NICKNAME_MAP.values():
        if n in variants:
            return variants
    return [name]


def get_nb_token(nation_slug: str) -> str:
    secret_key = db.secrets.get_secret(scope="api", key="surus_server_nb_secret").value
    resp = requests.get(
        f"https://server.surusenterprises.com/auth/api_token/{nation_slug}",
        headers={"x-api-key": secret_key},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def load_all_nations():
    if not WAREHOUSE_ID:
        return []
    try:
        result = db.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement="SELECT `group`, slug, state FROM universal.nb.source_nation_table ORDER BY `group`",
            wait_timeout="30s",
        )
        if result.status.state != StatementState.SUCCEEDED:
            return []
        cols = [c.name for c in result.manifest.schema.columns]
        rows = (result.result.data_array if result.result else None) or []
        print(f"Loaded {len(rows)} nations")
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        print(f"Failed to load nations: {e}")
        return []

ALL_NATIONS = load_all_nations()


@app.route("/search-nation")
@login_required
def search_nation():
    term = request.args.get("term", "").strip().lower()
    if not term:
        return jsonify({"success": False, "error": "Search term required"}), 400
    matches = [
        n for n in ALL_NATIONS
        if term in (n.get("group") or "").lower() or term in (n.get("slug") or "").lower()
    ]
    return jsonify({"success": True, "records": matches[:20]})


@app.route("/search-by-name")
@login_required
def search_by_name():
    first = request.args.get("first", "").strip()
    last = request.args.get("last", "").strip()
    nation_slug = request.args.get("nation_slug", "").strip()

    if not last or not nation_slug:
        return jsonify({"success": False, "error": "Last name and nation required"}), 400
    if not WAREHOUSE_ID:
        return jsonify({"success": False, "error": "Warehouse not configured"}), 500

    first_clean, _ = strip_suffix(first)
    last_clean, _ = strip_suffix(last)
    first_lower = first_clean.lower()

    def run_query(statement, params):
        result = db.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement=statement,
            parameters=params,
            wait_timeout="30s",
        )
        if result.status.state != StatementState.SUCCEEDED:
            raise Exception(result.status.error.message if result.status.error else "Query failed")
        cols = [c.name for c in result.manifest.schema.columns]
        rows = (result.result.data_array if result.result else None) or []
        return [dict(zip(cols, row)) for row in rows]

    select = """
        SELECT nb_id, first_name, last_name, full_name, suffix,
               COALESCE(`mailing_address.address1`, `registered_address.address1`, `home_address.address1`) AS address1,
               COALESCE(`mailing_address.city`, `registered_address.city`, `home_address.city`) AS city,
               COALESCE(`mailing_address.state`, `registered_address.state`, `home_address.state`) AS state,
               COALESCE(`mailing_address.zip`, `registered_address.zip`, `home_address.zip`) AS zip
        FROM universal.prod.signups
    """

    try:
        # Case 1: positional nickname (Trip, Trey, Deuce, Junior…) → search by last name + suffix
        if first_lower in POSITIONAL_NICKNAMES:
            suffix_val = POSITIONAL_NICKNAMES[first_lower]
            records = run_query(
                select + "WHERE LOWER(last_name) = LOWER(:last_name) AND LOWER(suffix) LIKE :suffix AND nation = :nation LIMIT 20",
                [
                    StatementParameterListItem(name="last_name", value=last_clean),
                    StatementParameterListItem(name="suffix", value=f"%{suffix_val}%"),
                    StatementParameterListItem(name="nation", value=nation_slug),
                ]
            )
            if records:
                return jsonify({"success": True, "records": records})

        # Case 2: normal nickname/name search with variants
        variants = get_name_variants(first_clean) if first_clean else [first_clean]
        conditions = " OR ".join([f"LOWER(first_name) = LOWER(:v{i})" for i in range(len(variants))])
        params = [StatementParameterListItem(name=f"v{i}", value=v) for i, v in enumerate(variants)]
        params += [
            StatementParameterListItem(name="last_name", value=last_clean),
            StatementParameterListItem(name="nation", value=nation_slug),
        ]
        records = run_query(
            select + f"WHERE ({conditions}) AND LOWER(last_name) = LOWER(:last_name) AND nation = :nation LIMIT 20",
            params
        )
        if records:
            return jsonify({"success": True, "records": records})

        # Case 3: fallback — last name only (catches unusual nicknames not in our map)
        records = run_query(
            select + "WHERE LOWER(last_name) = LOWER(:last_name) AND nation = :nation LIMIT 20",
            [
                StatementParameterListItem(name="last_name", value=last_clean),
                StatementParameterListItem(name="nation", value=nation_slug),
            ]
        )
        return jsonify({"success": True, "records": records, "fallback": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/search-signup")
@login_required
def search_signup():
    name = request.args.get("name", "").strip()
    nation_slug = request.args.get("nation_slug", "").strip()

    if not name or not nation_slug:
        return jsonify({"success": False, "error": "Name and nation slug are required"}), 400

    if not WAREHOUSE_ID:
        return jsonify({"success": False, "error": "DATABRICKS_WAREHOUSE_ID not configured"}), 500

    try:
        result = db.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement="""
                SELECT nb_id, first_name, last_name, full_name,
                       COALESCE(`mailing_address.address1`, `registered_address.address1`, `home_address.address1`) AS address1,
                       COALESCE(`mailing_address.city`, `registered_address.city`, `home_address.city`) AS city,
                       COALESCE(`mailing_address.state`, `registered_address.state`, `home_address.state`) AS state,
                       COALESCE(`mailing_address.zip`, `registered_address.zip`, `home_address.zip`) AS zip
                FROM universal.prod.signups
                WHERE full_name ILIKE :term
                  AND nation = :nation
                LIMIT 20
            """,
            parameters=[
                StatementParameterListItem(name="term", value=f"{strip_suffix(name)[0]}%"),
                StatementParameterListItem(name="nation", value=nation_slug),
            ],
            wait_timeout="30s",
        )

        if result.status.state != StatementState.SUCCEEDED:
            msg = result.status.error.message if result.status.error else "Query failed"
            return jsonify({"success": False, "error": msg}), 500

        cols = [c.name for c in result.manifest.schema.columns]
        rows = result.result.data_array or []
        records = [dict(zip(cols, row)) for row in rows]

        return jsonify({"success": True, "records": records})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


def parse_upload(file) -> pd.DataFrame:
    name = file.filename.lower()
    raw = file.read()
    if name.endswith(".csv") or name.endswith(".txt"):
        for kwargs in [
            {"sep": ",", "quotechar": '"', "engine": "python"},
            {"sep": ",", "quotechar": '"', "quoting": 0, "on_bad_lines": "skip"},
            {"sep": "\t", "engine": "python"},
            {"sep": "|", "engine": "python"},
            {"sep": ";", "engine": "python"},
            {"sep": ",", "on_bad_lines": "skip"},
        ]:
            try:
                df = pd.read_csv(io.BytesIO(raw), **kwargs)
                if len(df.columns) > 1:
                    return df
            except Exception:
                continue
        return pd.read_csv(io.BytesIO(raw), on_bad_lines="skip")
    elif name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(raw))
    elif name.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(raw))
        for table in doc.tables:
            headers = [c.text.strip() for c in table.rows[0].cells]
            rows = [[c.text.strip() for c in r.cells] for r in table.rows[1:]]
            if headers:
                return pd.DataFrame(rows, columns=headers)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return pd.DataFrame({"text": text.splitlines()})
    elif name.endswith(".json"):
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list):
            return pd.DataFrame(data)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return pd.DataFrame(v)
        return pd.DataFrame([data])
    elif name.endswith(".pdf"):
        try:
            import pdfplumber
            rows = []
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page in pdf.pages:
                    for table in (page.extract_tables() or []):
                        if table:
                            headers = table[0]
                            for row in table[1:]:
                                rows.append(dict(zip(headers, row)))
            if rows:
                return pd.DataFrame(rows)
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            return pd.DataFrame({"text": [l for l in text.splitlines() if l.strip()]})
        except ImportError:
            return pd.DataFrame({"error": ["Install pdfplumber to parse PDFs: pip install pdfplumber"]})
    else:
        try:
            return pd.read_csv(io.BytesIO(raw))
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
            return pd.DataFrame({"text": text.splitlines()})


def ai_map_and_clean(columns: list, all_rows: list) -> dict:
    prompt = f"""You are a data cleaning and mapping assistant. Your job is to convert uploaded contact data into clean NationBuilder contact records.

Valid NationBuilder contact fields:
- signup_id: the NationBuilder person ID being contacted (digits only, no extra text)
- author_id: the NationBuilder ID of who logged the contact (digits only)
- contact_method: must be exactly one of {json.dumps(CONTACT_METHODS)}
- contact_status: must be exactly one of {json.dumps(CONTACT_STATUSES)}
- content: free text notes about the interaction

Source columns: {json.dumps(columns)}

All rows to clean and convert: {json.dumps(all_rows)}

Instructions:
1. Map each source column to the appropriate NationBuilder field using common sense (e.g. "Method" → contact_method, "NB ID" → signup_id).
2. Fix ANY data quality issues you find — truncated values, merged fields, extra text in ID columns, inconsistent casing, etc. Use common sense: if signup_id contains "ction 2855313", the real ID is "2855313". If contact_status says "Meaningful Intera", fix it to "meaningful_interaction".
3. Normalize contact_method and contact_status values to valid NationBuilder equivalents (e.g. "Phone Call" → "phone_call", "No Answer" → "no_answer", "Meaningful Interaction" → "meaningful_interaction").
4. For first_name and last_name columns: do NOT map them as NationBuilder fields, but DO include them in each row as "_first_name" and "_last_name" so they can be used for person lookup when signup_id is missing.
5. Return one cleaned record per input row.

Return ONLY a JSON object:
{{
  "column_mapping": {{"source_column": "nb_field_or_null"}},
  "cleaned_rows": [
    {{"signup_id": "...", "_first_name": "...", "_last_name": "...", "contact_method": "...", "contact_status": "...", "content": "..."}},
    ...
  ],
  "notes": "brief summary of fixes applied"
}}"""

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "minimax/minimax-m3",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


@app.route("/bulk")
@login_required
def bulk():
    return render_template("bulk.html")


@app.route("/bulk/upload", methods=["POST"])
@login_required
def bulk_upload():
    if "file" not in request.files or request.files["file"].filename == "":
        return jsonify({"success": False, "error": "No file uploaded"}), 400
    try:
        df = parse_upload(request.files["file"])
        df = df.dropna(how="all").head(500)
        columns = list(df.columns)
        all_rows_raw = df.fillna("").astype(str).to_dict(orient="records")
        result = ai_map_and_clean(columns, all_rows_raw)
        cleaned = result.get("cleaned_rows", [])
        return jsonify({
            "success": True,
            "columns": columns,
            "mapping": result,
            "preview": cleaned[:10],
            "total_rows": len(cleaned),
            "all_rows": cleaned,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/bulk/import", methods=["POST"])
@login_required
def bulk_import():
    data = request.get_json()
    nation_slug = data.get("nation_slug", "").strip()
    rows = data.get("rows", [])
    imported_by = data.get("imported_by", "").strip()
    if not nation_slug:
        return jsonify({"success": False, "error": "Nation slug required"}), 400
    if not rows:
        return jsonify({"success": False, "error": "No rows to import"}), 400
    try:
        token = get_nb_token(nation_slug)
    except Exception as e:
        return jsonify({"success": False, "error": f"Auth failed: {e}"}), 500

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%B %d, %Y at %I:%M:%S %p UTC")
    import_tag = f"\n\n--- Bulk import by: {imported_by} | {timestamp} ---" if imported_by else f"\n\n--- Bulk import | {timestamp} ---"

    url = f"https://{nation_slug}.nationbuilder.com/api/v2/contacts"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    results = {"success": 0, "failed": 0, "errors": []}
    for i, row in enumerate(rows):
        attributes = {k: v for k, v in row.items()
                      if k in ("contact_method", "contact_status", "content")}
        existing_content = attributes.get("content", "") or ""
        if existing_content:
            attributes["content"] = f"{existing_content}\n\n{import_tag.strip()}"
        else:
            attributes["content"] = import_tag.strip()
        print(f"DEBUG row {i+1} content: {attributes['content'][:80]}")
        relationships = {}
        for field, rel in [("signup_id", "signup"), ("author_id", "author")]:
            if row.get(field):
                relationships[rel] = {"data": {"type": "signups", "id": str(row[field])}}
        body = {"data": {"type": "contacts", "attributes": attributes}}
        if relationships:
            body["data"]["relationships"] = relationships
        try:
            resp = requests.post(url, headers=headers, json=body)
            resp.raise_for_status()
            results["success"] += 1
        except requests.HTTPError as e:
            results["failed"] += 1
            detail = e.response.json() if e.response else str(e)
            results["errors"].append({"row": i + 1, "error": str(e), "detail": detail})
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"row": i + 1, "error": str(e)})
    return jsonify({"success": True, "results": results})


@app.route("/login")
def login():
    app_url = os.getenv("APP_URL", "http://localhost:5000")
    redirect_uri = app_url + "/auth/callback"
    return google.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    email = userinfo.get("email", "")
    if not email.endswith("@surusenterprises.com"):
        return render_template("login.html", error="Access restricted to Surus Enterprises accounts only.")
    user = User(
        id=email,
        email=email,
        name=userinfo.get("name", email),
        picture=userinfo.get("picture", ""),
    )
    _users[email] = user
    login_user(user, remember=True)
    return redirect("/")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


@app.route("/")
@login_required
def index():
    return render_template("index.html", contact_methods=CONTACT_METHODS, contact_statuses=CONTACT_STATUSES)


@app.route("/import", methods=["POST"])
@login_required
def import_contact():
    form = request.form
    nation_slug = form.get("nation_slug", "").strip()

    if not nation_slug:
        return jsonify({"success": False, "error": "Nation slug is required"}), 400

    attributes = {}

    relationships = {}

    for field, rel_type in [("author_id", "author"), ("signup_id", "signup"),
                             ("broadcaster_id", "broadcaster"), ("path_id", "path")]:
        val = form.get(field, "").strip()
        if val:
            relationships[rel_type] = {"data": {"type": "signups", "id": val}}

    path_step = form.get("path_step_id", "").strip()
    if path_step:
        relationships["path_step"] = {"data": {"type": "path_steps", "id": path_step}}

    content = form.get("content", "").strip()
    if content:
        attributes["content"] = content

    for field in ["contact_status", "contact_method"]:
        val = form.get(field, "").strip()
        if val:
            attributes[field] = val

    pc = form.get("pc_in_cents", "").strip()
    if pc:
        try:
            attributes["pc_in_cents"] = int(pc)
        except ValueError:
            return jsonify({"success": False, "error": "Political capital must be a whole number"}), 400

    body = {"data": {"type": "contacts", "attributes": attributes}}
    if relationships:
        body["data"]["relationships"] = relationships

    try:
        token = get_nb_token(nation_slug)
        url = f"https://{nation_slug}.nationbuilder.com/api/v2/contacts"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        return jsonify({"success": True, "data": resp.json()})
    except requests.HTTPError as e:
        detail = None
        if e.response is not None:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text
        return jsonify({"success": False, "error": str(e), "detail": detail}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)

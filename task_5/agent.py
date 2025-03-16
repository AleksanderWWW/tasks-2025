# Skeleton for Agent class
import random
import torch
import numpy as np

# Define role constants for better code readability
ROLE_EXPLORE = "explore"
ROLE_ATTACK = "attack"
ROLE_DEFEND = "defend"


def with_emergency_return(func):
    def inner(obs: dict, idx: int, home_planet: tuple, *args, **kwargs):
        ship = obs["allied_ships_dict"][idx]
        dx = abs(ship[1] - home_planet[0])
        dy = abs(ship[2] - home_planet[1])

        if dx + dy <= 100:
            home_occupation = obs["planets_occupation"][0][2]
            if home_planet[0] == 9 and home_occupation != 0:
                return return_home(ship, home_planet[0], home_planet[1])

            if home_planet[0] == 90 and home_occupation != 100:
                return return_home(ship, home_planet[0], home_planet[1])

        return func(obs, idx, home_planet, *args, **kwargs)

    return inner

class Agent:

    def load(self, abs_path: str):
        pass

    def eval(self):
        pass

    def to(self, device):
        pass

    def __init__(self, side: int):
        self.side = side
        # Initialize home_planet and enemy_planet as None
        # They will be set on the first call to get_action and never changed again
        self.home_planet = None
        self.enemy_planet = None
        
        # Initialize the ship roles dictionary
        self.ship_roles = {}
        
        # Game state tracking
        self.turn_counter = 0
        self.last_known_enemy_positions = {}
        self.planet_targets = {}  # Track which neutral planets are being targeted
        
        # Track explorer ship behavior
        self.ship_bump_count = {}  # Track border bumps for each ship
        self.ship_directions = {}  # Current movement directions for each ship
        self.explore_direction_counter = 0  # Counter for assigning exploration directions

    def get_action(self, obs: dict) -> dict:
        """
        Main function, which gets called during step() of the environment.

        Observation space:
            game_map: whole grid of board_size, which already has applied visibility mask on it
            allied_ships: an array of all currently available ships for the player. The ships are represented as a list:
                (ship id, position x, y, current health points, firing_cooldown, move_cooldown)
                - ship id: int [0, 1000]
                - position x: int [0, 100]
                - position y: int [0, 100]
                - health points: int [1, 100]
                - firing_cooldown: int [0, 10]
                - move_cooldown: int [0, 3]
            enemy_ships: same, but for the opposing player ships
            planets_occupation: for each visible planet, it shows the occupation progress:
                - planet_x: int [0, 100]
                - planet_y: int [0, 100]
                - occupation_progress: int [-1, 100]:
                    -1: planet is unoccupied
                    0: planet occupied by the 1st player
                    100: planet occupied by the 2nd player
                    Values between indicate an ongoing conflict for the ownership of the planet
            resources: current resources available for building

        Action space:
            ships_actions: player can provide an action to be executed by every of his ships.
                The command looks as follows:
                - ship_id: int [0, 1000]
                - action_type: int [0, 1]
                    0 - move
                    1 - fire
                - direction: int [0, 3] - direction of movement or firing
                    0 - right
                    1 - down
                    2 - left
                    3 - up
                - speed (not applicable when firing): int [0, 3] - a number of fields to move
            construction: int [0, 10] - a number of ships to be constructed

        :param obs:
        :return:
        """
    
        game_map = obs.get('map')
        allied_ships = obs.get('allied_ships')
        enemy_ships = obs.get('enemy_ships')
        planets_occupation = obs.get('planets_occupation')
        resources = obs.get('resources')
        
        # Increment turn counter
        self.turn_counter += 1

        # Set home_planet and enemy_planet only once on the first call
        if self.home_planet is None and planets_occupation:
            self.home_planet = planets_occupation[0]

            # Determine enemy planet based on home planet location
            if self.home_planet[0] == 9:
                self.enemy_planet = (90, 90)
            else:
                self.enemy_planet = (9, 9)

        # Create dictionary for easier access to ships by ID
        allied_ship_dict = {}
        for ship in allied_ships:
            ship_id = ship[0]
            allied_ship_dict[ship_id] = ship
        
        # Store the ship dictionary in obs for use by action functions
        obs["allied_ships_dict"] = allied_ship_dict
        
        # Update the enemy ship positions for tracking
        if enemy_ships:
            for enemy in enemy_ships:
                self.last_known_enemy_positions[enemy[0]] = (enemy[1], enemy[2], self.turn_counter)
        
        # Run the scheduler to update ship roles - pass the original allied_ships list
        # ALL role management should happen in the scheduler
        self.scheduler(obs, allied_ships)

        action_list = []
        # Iterate through the original allied_ships list
        for ship in allied_ships:
            ship_id = ship[0]
            
            # Get the ship's role from the dictionary - the scheduler should have assigned one
            role = self.ship_roles[ship_id]  # Using direct lookup since scheduler ensures all ships have roles
            
            # Execute the appropriate action based on the ship's role
            if role == ROLE_DEFEND:
                action_list.append(get_defense_action(obs, ship_id, self.home_planet))
            elif role == ROLE_EXPLORE:
                action_list.append(get_explore_action(obs, ship_id, self.home_planet, self))
            elif role == ROLE_ATTACK:
                action_list.append(get_offense_action(obs, ship_id, self.enemy_planet))
            else:
                # This should never happen if scheduler is working correctly
                action_list.append(get_explore_action(obs, ship_id, self.home_planet, self))

        return {
            "ships_actions": action_list,
            "construction": 10
        }
    
    def scheduler(self, obs: dict, allied_ships):
        """
        Assigns and updates roles for ships based on the current game state.
        This function dynamically manages ship roles throughout the game.
        
        Logic:
        1. Ensure all ships have an initial role
        2. Reassign roles based on game state (enemy presence, planets, etc.)
        3. Balance roles to maintain a good distribution of explorers, attackers, and defenders
        
        :param obs: The current game observation
        :param allied_ships: List of allied ships
        """
        enemy_ships = obs.get('enemy_ships')
        planets_occupation = obs.get('planets_occupation')
        
        # Track the count of each role for balancing
        role_counts = {ROLE_EXPLORE: 0, ROLE_ATTACK: 0, ROLE_DEFEND: 0}
        
        
        # Step 1: ALWAYS ensure all ships have an initial role
        for ship in allied_ships:
            ship_id = ship[0]  # Extract the ID from the ship
            
            # Check if the ship already has a role
            if ship_id not in self.ship_roles:
                # Assign initial role based on ID (for compatibility with original logic)
                if ship_id % 3 == 2:
                    self.ship_roles[ship_id] = ROLE_DEFEND
                elif ship_id % 3 == 0:
                    self.ship_roles[ship_id] = ROLE_EXPLORE
                else:
                    self.ship_roles[ship_id] = ROLE_ATTACK

            
            # Count the current distribution of roles
            current_role = self.ship_roles[ship_id]
            role_counts[current_role] = role_counts.get(current_role, 0) + 1
        
        # # Get the total number of ships
        total_ships = len(allied_ships)
        
        # Step 3: Balance roles based on game state
        
        # Early game strategy (first 50 turns)
        if self.turn_counter < 250:
            target_distribution = {
                ROLE_EXPLORE: max(1, int(total_ships * 0.8)),  # 0% explorers
                ROLE_ATTACK: max(0, int(total_ships * 0)),   # 0% attackers
                ROLE_DEFEND: max(0, int(total_ships * 0.2))    # 0% defenders
            }
        # Mid game strategy
        elif 250 <= self.turn_counter < 750:
            target_distribution = {
                ROLE_EXPLORE: max(0, int(total_ships * 0.8)),  # 30% explorers
                ROLE_ATTACK: max(1, int(total_ships * 0)),   # 40% attackers
                ROLE_DEFEND: max(0, int(total_ships * 0.2))    # 30% defenders
            }
        # Late game strategy
        else:
            target_distribution = {
                ROLE_EXPLORE: max(1, int(total_ships * 0)),  # 20% explorers
                ROLE_ATTACK: max(0, int(total_ships * 1.0)),   # 50% attackers
                ROLE_DEFEND: max(1, int(total_ships * 0))    # 30% defenders
            }
        
        # Adjust for enemy presence near home base
        # if enemy_near_home:
        #     # Allocate more ships to defense if enemies are near home
        #     target_distribution[ROLE_DEFEND] = max(2, int(total_ships * 0.5))
        #     target_distribution[ROLE_ATTACK] = max(1, int(total_ships * 0.3))
        #     target_distribution[ROLE_EXPLORE] = max(1, total_ships - target_distribution[ROLE_DEFEND] - target_distribution[ROLE_ATTACK])
        
        # Make adjustments to achieve the target distribution
        for role, target_count in target_distribution.items():
            current_count = role_counts.get(role, 0)
            
            # If we need more ships in this role
            while current_count < target_count:
                # Find a role that has excess ships
                for excess_role in role_counts:
                    if excess_role != role and role_counts[excess_role] > target_distribution[excess_role]:
                        # Find a ship to reassign
                        for ship in allied_ships:
                            ship_id = ship[0]
                            if self.ship_roles.get(ship_id) == excess_role:
                                # Consider health before reassigning
                                ship_health = ship[3]
                                
                                # Don't reassign critically damaged ships to attack
                                if role == ROLE_ATTACK and ship_health < 30:
                                    continue
                                    
                                # Reassign the ship
                                self.ship_roles[ship_id] = role
                                role_counts[excess_role] -= 1
                                role_counts[role] += 1
                                current_count += 1                                
                                break
                        
                        # If we've reached the target, break out
                        if current_count >= target_count:
                            break
                
                # If we can't find any more ships to reassign, just break
                if current_count < target_count:
                    break
        
        # Cleanup: Remove entries for ships that no longer exist
        ship_ids = [ship[0] for ship in allied_ships]
        for ship_id in list(self.ship_roles.keys()):
            if ship_id not in ship_ids:
                del self.ship_roles[ship_id]

@with_emergency_return
def get_offense_action(obs: dict, idx: int, enemy_planet: tuple) -> list[int]:
    ship = obs["allied_ships_dict"][idx]
    ship_id, ship_x, ship_y = ship[0], ship[1], ship[2]
    enemy_x, enemy_y = enemy_planet[0], enemy_planet[1]
    
    # Only try to shoot if firing cooldown is 0
    if ship[4] == 0:  # ship[4] is firing_cooldown
        for enemy in obs["enemy_ships"]:
            choice = shoot_enemy_if_in_range(enemy, ship)
            if choice:
                return choice
    
    # If we can't shoot or firing is on cooldown, move towards enemy planet
    # Determine direction to move towards enemy planet
    dx = enemy_x - ship_x
    dy = enemy_y - ship_y
    
    # Choose direction based on which axis has greater distance
    if abs(dx) > abs(dy):
        # Move horizontally
        if dx > 0:
            direction = 0  # Right
        else:
            direction = 2  # Left
    else:
        # Move vertically
        if dy > 0:
            direction = 1  # Down
        else:
            direction = 3  # Up
    
    # Calculate movement speed based on cooldown
    # Always move with at least speed 1, regardless of cooldown
    # If the ship is in an asteroid field (cooldown > 0), it will move at 1/3 the normal speed
    # due to the game's cooldown mechanics (cooldown of 3 makes ship 3x slower)
    if ship[5] == 0:  # No cooldown - can move at full speed (up to 3)
        speed = 3  # Maximum speed when no cooldown
    else:
        # Ship has cooldown but can still move - just slower
        # We always want to move with speed 1 even with cooldown
        speed = 1
    
    return [ship_id, 0, direction, speed]

@with_emergency_return
def get_explore_action(obs: dict, idx: int, home_planet: tuple, agent) -> list[int]:
    """
    Function to explore the map with three distinct exploration patterns:
    1. Mainly horizontal with slight vertical (mainly left/right with slight up/down)
    2. Mainly vertical with slight horizontal (mainly up/down with slight left/right)
    3. Diagonal (equal horizontal and vertical)
    
    Ships are assigned these patterns sequentially and will bounce off borders.
    After two border bumps, ships change their role to attacker.
    
    The agent parameter provides access to the agent's state for tracking exploration patterns.
    """
    ship = obs["allied_ships_dict"][idx]
    ship_id = ship[0]
    ship_x, ship_y = ship[1], ship[2]
    
    # Only try to shoot if firing cooldown is 0
    if ship[4] == 0:  # ship[4] is firing_cooldown
        for enemy in obs["enemy_ships"]:
            choice = shoot_enemy_if_in_range(enemy, ship)
            if choice:
                return choice

    # Look for valuable targets first (maintain this logic)
    found = False
    target_x, target_y = None, None
    max_ones_count = -1
    
    # Search for valuable tiles
    for i in range(len(obs['map'])):
        for j in range(len(obs['map'][i])):
            if format(obs['map'][i][j], '08b')[-1] == '1' and format(obs['map'][i][j], '08b')[0:2] == '00':
                ones_count = sum(
                    1 for x in range(max(0, i-3), min(len(obs['map']), i+3))
                    for y in range(max(0, j-3), min(len(obs['map'][i]), j+3))
                    if format(obs['map'][x][y], '08b')[-1] == '1' and format(obs['map'][x][y], '08b')[0:2] == '00'
                )
                if ones_count > max_ones_count:
                    max_ones_count = ones_count
                    target_x, target_y = j, i
                    found = True

    if not found:
        # Initialize pattern for this ship if not already set
        if ship_id not in agent.ship_directions:
            # Initialize ship_patterns dictionary if it doesn't exist
            if not hasattr(agent, 'ship_patterns'):
                agent.ship_patterns = {}
                
            # Assign exploration pattern sequentially - 0, 1, or 2
            explore_pattern = agent.explore_direction_counter % 3
            agent.explore_direction_counter += 1
            
            # Store the pattern type for this ship
            agent.ship_patterns[ship_id] = explore_pattern
                        
            # Define the pattern based on starting position
            if home_planet[0] == 9:  # Starting from (9,9)
                # For 9,9 start, mirror the patterns (mainly right and slightly down, etc.)
                if explore_pattern == 0:
                    # Pattern 1: Mainly RIGHT with slight DOWN
                    agent.ship_directions[ship_id] = {
                        'primary': 0,  # Right
                        'secondary': 1,  # Down
                        'primary_weight': 4,  # 4 moves in primary direction
                        'secondary_weight': 1  # 1 move in secondary direction
                    }
                elif explore_pattern == 1:
                    # Pattern 2: Mainly DOWN with slight RIGHT
                    agent.ship_directions[ship_id] = {
                        'primary': 1,  # Down
                        'secondary': 0,  # Right
                        'primary_weight': 4,  # 4 moves in primary direction
                        'secondary_weight': 1  # 1 move in secondary direction
                    }
                else:  # Pattern 3: Diagonal (equal RIGHT and DOWN)
                    agent.ship_directions[ship_id] = {
                        'primary': 0,  # Right
                        'secondary': 1,  # Down
                        'primary_weight': 1,  # Equal weight
                        'secondary_weight': 1  # Equal weight
                    }
            else:  # Starting from (90,90)
                if explore_pattern == 0:
                    # Pattern 1: Mainly LEFT with slight UP
                    agent.ship_directions[ship_id] = {
                        'primary': 2,  # Left
                        'secondary': 3,  # Up
                        'primary_weight': 4,  # 4 moves in primary direction
                        'secondary_weight': 1  # 1 move in secondary direction
                    }
                elif explore_pattern == 1:
                    # Pattern 2: Mainly UP with slight LEFT
                    agent.ship_directions[ship_id] = {
                        'primary': 3,  # Up
                        'secondary': 2,  # Left
                        'primary_weight': 4,  # 4 moves in primary direction
                        'secondary_weight': 1  # 1 move in secondary direction
                    }
                else:  # Pattern 3: Diagonal (equal LEFT and UP)
                    agent.ship_directions[ship_id] = {
                        'primary': 2,  # Left
                        'secondary': 3,  # Up
                        'primary_weight': 1,  # Equal weight
                        'secondary_weight': 1  # Equal weight
                    }
            
            # Initialize bump count
            agent.ship_bump_count[ship_id] = 0
                    
        # Get the current directions for this ship
        ship_pattern = agent.ship_directions[ship_id]
        pattern_type = agent.ship_patterns[ship_id]
        
        # Check if we're at a border and need to bump
        hit_border = False
        
        # Check horizontal borders
        if (ship_pattern['primary'] == 0 and ship_x >= 99) or (ship_pattern['primary'] == 2 and ship_x <= 0):
            # Swap horizontal direction (0 <-> 2)
            ship_pattern['primary'] = 2 if ship_pattern['primary'] == 0 else 0
            hit_border = True
        
        if (ship_pattern['secondary'] == 0 and ship_x >= 99) or (ship_pattern['secondary'] == 2 and ship_x <= 0):
            # Swap horizontal direction (0 <-> 2)
            ship_pattern['secondary'] = 2 if ship_pattern['secondary'] == 0 else 0
            hit_border = True
            
        # Check vertical borders
        if (ship_pattern['primary'] == 1 and ship_y >= 99) or (ship_pattern['primary'] == 3 and ship_y <= 0):
            # Swap vertical direction (1 <-> 3)
            ship_pattern['primary'] = 3 if ship_pattern['primary'] == 1 else 1
            hit_border = True
        
        if (ship_pattern['secondary'] == 1 and ship_y >= 99) or (ship_pattern['secondary'] == 3 and ship_y <= 0):
            # Swap vertical direction (1 <-> 3)
            ship_pattern['secondary'] = 3 if ship_pattern['secondary'] == 1 else 1
            hit_border = True
        
        # If we hit a border, increment bump count
        if hit_border:
            agent.ship_bump_count[ship_id] = agent.ship_bump_count.get(ship_id, 0) + 1
            
            # If this is the second bump, change the ship's role
            if agent.ship_bump_count[ship_id] >= 2:
                # Change role to attacker
                agent.ship_roles[ship_id] = ROLE_ATTACK
                # Return an attack action immediately
                return get_offense_action(obs, idx, agent.enemy_planet)
        
        # Determine movement based on pattern weights and turn counter
        primary_weight = ship_pattern['primary_weight']
        secondary_weight = ship_pattern['secondary_weight']
        total_weight = primary_weight + secondary_weight
        
        # Use a cycle based on weights to determine which direction to move
        step_in_cycle = (agent.turn_counter + ship_id) % total_weight
        
        # Choose direction based on step in cycle
        if step_in_cycle < primary_weight:
            direction = ship_pattern['primary']
        else:
            direction = ship_pattern['secondary']
        
        # Calculate speed - move faster when no cooldown
        speed = 1 if ship[5] > 0 else 3
        
        return [ship_id, 0, direction, speed]
    else:
        # Go towards the identified target
        # Note: The map coordinates and ship coordinates might be flipped (x,y vs y,x)
        dx = ship[1] - random.choice([target_x +2, target_x-2])  # X distance (ship x - target y)
        dy = ship[2] - random.choice([target_y +2, target_y-2])  # Y distance (ship y - target x)

        if abs(dx) > abs(dy):
            if dx > 0:
                return [ship[0], 0, 2, min(3, abs(dx))]  # Move left
            else:
                return [ship[0], 0, 0, min(3, abs(dx))]  # Move right
        else:
            if dy > 0:
                return [ship[0], 0, 3, min(3, abs(dy))]  # Move up
            else:
                return [ship[0], 0, 1, min(3, abs(dy))]  # Move down


@with_emergency_return
def get_defense_action(obs: dict, idx: int, home_planet: tuple) -> list[int]:
    ship = obs["allied_ships_dict"][idx]

    for enemy in obs["enemy_ships"]:
        choice = shoot_enemy_if_in_range(enemy, ship)
        if choice:
            return choice

    if ship[3] <= 30:
        return return_home(ship, home_planet[0], home_planet[1])

    return move_randomly_around_home(obs, ship, home_planet[0], home_planet[1])


def shoot_enemy_if_in_range(enemy, ship) -> list[int]:
    """
    Check if an enemy ship is within firing range (8 tiles) and directly aligned
    (same row or column) with our ship.
    
    Ship position: (ship[1], ship[2])
    Enemy position: (enemy[1], enemy[2])
    
    Returns a firing action if enemy is in range, otherwise an empty list.
    """
    ship_x, ship_y = ship[1], ship[2]
    enemy_x, enemy_y = enemy[1], enemy[2]
    
    # Check if ships are in the same row (y-coordinate)
    if ship_y == enemy_y:
        # Enemy is to the right of our ship
        if enemy_x > ship_x and enemy_x - ship_x <= 8:
            return [ship[0], 1, 0]  # Shoot right
        
        # Enemy is to the left of our ship
        if enemy_x < ship_x and ship_x - enemy_x <= 8:
            return [ship[0], 1, 2]  # Shoot left
    
    # Check if ships are in the same column (x-coordinate)
    if ship_x == enemy_x:
        # Enemy is below our ship
        if enemy_y > ship_y and enemy_y - ship_y <= 8:
            return [ship[0], 1, 1]  # Shoot down
        
        # Enemy is above our ship
        if enemy_y < ship_y and ship_y - enemy_y <= 8:
            return [ship[0], 1, 3]  # Shoot up
    
    # Enemy not in range or not aligned
    return []

def move_randomly_around_home(obs : dict, ship, home_x, home_y, max_distance=15) -> list[int]:
    """
    Poruszanie się losowo w obszarze max_distance wokół planety macierzystej.
    """
    ship_x, ship_y = ship[1], ship[2]

    for _ in range(10):
        if home_x == 9 and ship_x <= home_x:
            direction = 0
        elif home_y == 9 and ship_y <= home_y:
            direction = 1
        elif home_x == 90 and ship_x >= home_x:
            direction = 2
        elif home_y == 90 and ship_y >= home_y:
            direction = 3
        else:
            # Losowy wybór kierunku
            direction = random.randint(0, 3)

        # Przewidywana nowa pozycja
        new_x = ship_x + (1 if direction == 0 else -1 if direction == 2 else 0)
        new_y = ship_y + (1 if direction == 1 else -1 if direction == 3 else 0)

        if not (0 <= new_x < 100 and 0 <= new_y < 100):
            continue  # Jeśli poza mapą, ponawiamy próbę

        # Sprawdzenie, czy nowa pozycja mieści się w dozwolonym obszarze wokół planety
        if abs(new_x - home_x) + abs(new_y - home_y) > max_distance:
            continue  # Jeśli poza zakresem, ponawiamy próbę

        # Sprawdzenie, czy pole NIE jest asteroidą
        if is_asteroid(obs, new_x, new_y):
            continue  # Jeśli to asteroida, ponawiamy próbę

        # Jeśli pole jest poprawne, wykonaj ruch
        return [ship[0], 0, direction, 1]  # Ruch o 1 pole w danym kierunku
    
    return [ship[0], 0, direction, 1] 


def return_home(ship, home_x, home_y) -> list[int]:
    dx = ship[1] - home_x
    dy = ship[2] - home_y

    if abs(dx) > abs(dy):
        # need to move in X direction first
        if dx > 0:
            # need to move left
            return [ship[0], 0, 2, min(3, abs(dx))]
        else:
            # need to move right
            return [ship[0], 0, 0, min(3, abs(dx))]
    else:
        # need to move in Y direction first
        if dy > 0:
            # need to move up
            return [ship[0], 0, 3, min(3, abs(dy))]
        else:
            # need to move down
            return [ship[0], 0, 1, min(3, abs(dy))]
        
def is_asteroid(obs: dict, x, y) -> bool:

    point = obs['map'][x][y]
    
    if format(point, '08b')[-2] == '1':
        return True

    return False

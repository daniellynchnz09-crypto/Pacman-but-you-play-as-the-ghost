import pygame
import sys
import time

# Test the scoring logic directly

class TestGame:
    def __init__(self):
        self.score = 1000
        self.start_time = pygame.time.get_ticks()
        self.last_score_deduction = 0
        
    def update_score(self):
        now = pygame.time.get_ticks()
        if now - self.start_time > 0:
            time_elapsed = (now - self.start_time) // 1000
            
            # Only deduct once per second
            if time_elapsed > self.last_score_deduction:
                # Deduct 8.33 points per second (1000/120)
                self.score = max(0, self.score - 1000/120)
                self.last_score_deduction = time_elapsed
                return True
        return False

# Test the scoring
pygame.init()
test_game = TestGame()

print("Testing scoring system...")
print(f"Initial score: {test_game.score}")

# Simulate 10 seconds of gameplay
start_time = time.time()
for i in range(10):
    time.sleep(1)  # Wait 1 second
    test_game.update_score()
    print(f"After {i+1} second(s): {test_game.score:.1f} points")
    
print(f"Final score after 10 seconds: {test_game.score:.1f}")
print(f"Time to deplete: {1000/(1000/120):.1f} seconds")

pygame.quit()
